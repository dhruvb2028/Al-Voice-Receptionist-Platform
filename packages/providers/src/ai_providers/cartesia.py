"""Cartesia streaming TTS provider.

Implements the TTSProvider contract over Cartesia's realtime WebSocket:
incremental text in, Twilio-compatible µ-law 8 kHz audio out,
first-byte latency measurement, and immediate cancellation (barge-in
drops pending audio rather than flushing it).
"""

import asyncio
import base64
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator

import structlog
import websockets

from ai_providers.errors import ProviderAuthError, ProviderUnavailableError
from ai_providers.tts import TTSChunk

logger = structlog.get_logger()

CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket"
CARTESIA_VERSION = "2024-06-10"
CONNECT_TIMEOUT_SECONDS = 5.0


class CartesiaTTSSession:
    """One synthesis session; text streams in, µ-law frames stream out."""

    def __init__(
        self,
        *,
        api_key: str,
        voice_id: str,
        sample_rate: int,
        encoding: str,
        model_id: str = "sonic-2",
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self.voice_id = voice_id
        self._sample_rate = sample_rate
        self._encoding = encoding
        self._model_id = model_id
        self._connect_timeout = connect_timeout
        self._ws: websockets.ClientConnection | None = None
        self._context_id = f"ctx_{uuid.uuid4().hex[:12]}"
        self.cancelled = False
        self.closed = False
        self._first_byte_at: float | None = None
        self._opened_at = time.perf_counter()

    async def _connect(self) -> websockets.ClientConnection:
        if self._ws is not None:
            return self._ws
        url = f"{CARTESIA_WS_URL}?api_key={self._api_key}&cartesia_version={CARTESIA_VERSION}"
        try:
            async with asyncio.timeout(self._connect_timeout):
                self._ws = await websockets.connect(url)
        except TimeoutError as exc:
            raise ProviderUnavailableError(
                "cartesia connect timed out", provider="cartesia"
            ) from exc
        except websockets.InvalidStatus as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise ProviderAuthError(
                    f"cartesia auth failed ({status})", provider="cartesia"
                ) from exc
            raise ProviderUnavailableError(
                f"cartesia rejected connection ({status})", provider="cartesia"
            ) from exc
        except OSError as exc:
            raise ProviderUnavailableError(str(exc), provider="cartesia") from exc
        return self._ws

    def _payload(self, text: str, *, continue_: bool) -> str:
        return json.dumps(
            {
                "model_id": self._model_id,
                "transcript": text,
                "voice": {"mode": "id", "id": self.voice_id},
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_mulaw" if self._encoding == "mulaw" else self._encoding,
                    "sample_rate": self._sample_rate,
                },
                "context_id": self._context_id,
                "continue": continue_,
                "language": "en",
            }
        )

    async def send_text(self, text: str) -> None:
        if self.cancelled or self.closed or not text.strip():
            return
        ws = await self._connect()
        await ws.send(self._payload(text, continue_=True))

    async def flush(self) -> None:
        """Signal the end of the utterance so trailing audio renders."""
        if self.cancelled or self.closed:
            return
        ws = await self._connect()
        await ws.send(self._payload("", continue_=False))

    async def chunks(self) -> AsyncIterator[TTSChunk]:
        ws = await self._connect()
        while not self.cancelled and not self.closed:
            try:
                raw = await ws.recv()
            except websockets.ConnectionClosed:
                return
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("cartesia_malformed_event")
                continue
            if event.get("context_id") not in (None, self._context_id):
                continue  # stale context after cancellation
            event_type = event.get("type")
            if event_type == "chunk" and event.get("data"):
                first = self._first_byte_at is None
                if first:
                    self._first_byte_at = time.perf_counter()
                yield TTSChunk(
                    audio=base64.b64decode(event["data"]),
                    first_byte_ms=(
                        int((self._first_byte_at - self._opened_at) * 1000)
                        if first and self._first_byte_at is not None
                        else None
                    ),
                )
            elif event_type == "done":
                return
            elif event_type == "error":
                raise ProviderUnavailableError(
                    str(event.get("error", "cartesia error")), provider="cartesia"
                )

    async def cancel(self) -> None:
        """Barge-in: stop generation; pending audio is dropped."""
        self.cancelled = True
        if self._ws is not None:
            with contextlib.suppress(websockets.WebSocketException):  # pragma: no cover
                await self._ws.send(json.dumps({"context_id": self._context_id, "cancel": True}))
        # A fresh context isolates any future speech from stale audio.
        self._context_id = f"ctx_{uuid.uuid4().hex[:12]}"

    async def close(self) -> None:
        self.closed = True
        if self._ws is not None:
            with contextlib.suppress(websockets.WebSocketException):  # pragma: no cover
                await self._ws.close()
            self._ws = None


class CartesiaTTSProvider:
    """TTSProvider implementation."""

    def __init__(self, *, api_key: str, model_id: str = "sonic-2") -> None:
        self._api_key = api_key
        self._model_id = model_id

    async def open_session(
        self, *, voice_id: str, sample_rate: int, encoding: str
    ) -> CartesiaTTSSession:
        return CartesiaTTSSession(
            api_key=self._api_key,
            voice_id=voice_id,
            sample_rate=sample_rate,
            encoding=encoding,
            model_id=self._model_id,
        )
