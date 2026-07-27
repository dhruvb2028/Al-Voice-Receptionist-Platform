"""Deepgram streaming STT provider.

Implements the STTProvider contract over Deepgram's realtime WebSocket:
µ-law 8 kHz input, interim + final transcripts with confidence and word
timing, punctuation, provider request IDs, bounded reconnection, and
hard timeouts. Endpointing decisions live in the voice service
(``voice.endpointing``) — this adapter surfaces the raw signals
(``speech_final``, ``UtteranceEnd``) it needs.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import structlog
import websockets

from ai_providers.errors import ProviderAuthError, ProviderUnavailableError
from ai_providers.stt import TranscriptEvent

logger = structlog.get_logger()

DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"
CONNECT_TIMEOUT_SECONDS = 5.0
MAX_RECONNECT_ATTEMPTS = 2


class DeepgramSTTSession:
    """One realtime transcription session."""

    def __init__(
        self,
        *,
        api_key: str,
        sample_rate: int,
        encoding: str,
        language: str,
        model: str = "nova-3",
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._params = {
            "model": model,
            "language": language,
            "encoding": encoding,
            "sample_rate": str(sample_rate),
            "channels": "1",
            "punctuate": "true",
            "interim_results": "true",
            "smart_format": "true",
            "vad_events": "true",
            "utterance_end_ms": "1000",
        }
        self._connect_timeout = connect_timeout
        self._ws: websockets.ClientConnection | None = None
        self.closed = False
        self.request_id: str | None = None
        self._reconnects = 0
        self._session_started = time.perf_counter()

    async def _connect(self) -> None:
        url = f"{DEEPGRAM_WS_URL}?{urlencode(self._params)}"
        try:
            async with asyncio.timeout(self._connect_timeout):
                self._ws = await websockets.connect(
                    url,
                    additional_headers={"Authorization": f"Token {self._api_key}"},
                )
        except TimeoutError as exc:
            raise ProviderUnavailableError(
                "deepgram connect timed out", provider="deepgram"
            ) from exc
        except websockets.InvalidStatus as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise ProviderAuthError(
                    f"deepgram auth failed ({status})", provider="deepgram"
                ) from exc
            raise ProviderUnavailableError(
                f"deepgram rejected connection ({status})", provider="deepgram"
            ) from exc
        except OSError as exc:
            raise ProviderUnavailableError(str(exc), provider="deepgram") from exc
        response_headers = getattr(self._ws, "response", None)
        if response_headers is not None:
            self.request_id = response_headers.headers.get("dg-request-id")
        logger.info("deepgram_connected", request_id=self.request_id)

    async def _ensure_connected(self) -> websockets.ClientConnection:
        if self._ws is None:
            await self._connect()
        assert self._ws is not None
        return self._ws

    async def send_audio(self, chunk: bytes) -> None:
        if self.closed:
            raise ProviderUnavailableError("session closed", provider="deepgram")
        ws = await self._ensure_connected()
        try:
            await ws.send(chunk)
        except websockets.ConnectionClosed as exc:
            # Bounded reconnection: the stream resumes with fresh context;
            # in-flight audio in Deepgram's buffer is lost but the caller
            # keeps talking, so newer audio matters more.
            self._ws = None
            self._reconnects += 1
            if self._reconnects > MAX_RECONNECT_ATTEMPTS:
                raise ProviderUnavailableError("deepgram stream lost", provider="deepgram") from exc
            logger.warning("deepgram_reconnecting", attempt=self._reconnects)
            ws = await self._ensure_connected()
            await ws.send(chunk)

    def _parse_event(self, raw: str | bytes) -> TranscriptEvent | None:
        try:
            payload: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("deepgram_malformed_event")
            return None

        event_type = payload.get("type")
        if event_type == "UtteranceEnd":
            # Surface as an empty final marker: the endpointing engine
            # treats it as a strong end-of-turn signal.
            return TranscriptEvent(text="", is_final=True, finalization_ms=0)
        if event_type != "Results":
            return None

        channel = payload.get("channel", {})
        alternatives = channel.get("alternatives") or [{}]
        alternative = alternatives[0]
        text = alternative.get("transcript", "")
        words = alternative.get("words") or []
        start_ms = int(words[0]["start"] * 1000) if words else None
        end_ms = int(words[-1]["end"] * 1000) if words else None
        is_final = bool(payload.get("is_final"))
        speech_final = bool(payload.get("speech_final"))
        if not text and not is_final:
            return None
        return TranscriptEvent(
            text=text,
            # speech_final marks a Deepgram-endpointed utterance; a bare
            # is_final is a finalized fragment that may continue.
            is_final=speech_final or is_final,
            confidence=alternative.get("confidence"),
            start_ms=start_ms,
            end_ms=end_ms,
            finalization_ms=int(payload.get("duration", 0) * 1000) if is_final else None,
        )

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        ws = await self._ensure_connected()
        while not self.closed:
            try:
                raw = await ws.recv()
            except websockets.ConnectionClosed:
                if self.closed:  # closed concurrently by close()
                    return  # type: ignore[unreachable]
                self._ws = None
                self._reconnects += 1
                if self._reconnects > MAX_RECONNECT_ATTEMPTS:
                    raise ProviderUnavailableError(
                        "deepgram stream lost", provider="deepgram"
                    ) from None
                logger.warning("deepgram_reconnecting", attempt=self._reconnects)
                ws = await self._ensure_connected()
                continue
            event = self._parse_event(raw)
            if event is not None:
                yield event

    async def close(self) -> None:
        self.closed = True
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except websockets.WebSocketException:  # pragma: no cover
                pass
            self._ws = None


class DeepgramSTTProvider:
    """STTProvider implementation."""

    def __init__(self, *, api_key: str, model: str = "nova-3") -> None:
        self._api_key = api_key
        self._model = model

    async def connect(
        self, *, sample_rate: int, encoding: str, language: str
    ) -> DeepgramSTTSession:
        session = DeepgramSTTSession(
            api_key=self._api_key,
            sample_rate=sample_rate,
            encoding=encoding,
            language=language,
            model=self._model,
        )
        await session._ensure_connected()
        return session
