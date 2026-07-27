"""Twilio Media Streams WebSocket endpoint.

Security model: the connection is authorized solely by the signed,
single-use call token minted by the API at webhook time. Stream
metadata (customParameters, start payloads) is NEVER trusted for tenant
identity — the trusted call context is re-resolved from the database by
the token's call SID, and the start event's CallSid must match.

Audio path: inbound µ-law 8 kHz frames land in a bounded queue with
drop-oldest backpressure (a stalled consumer must never balloon
memory); outbound audio is streamed back as media events and can be
cleared instantly on barge-in. STT/LLM/TTS wiring attaches to the
bridge in the streaming milestones.
"""

import asyncio
import base64
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from ai_shared.call_tokens import CallTokenError, verify_call_token
from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

logger = structlog.get_logger()

AUDIO_QUEUE_MAX_FRAMES = 200  # ~4s of 20ms frames
IDLE_TIMEOUT_SECONDS = 30.0
INVALID_CONNECTION_LIMIT = 10
INVALID_CONNECTION_WINDOW_SECONDS = 60.0

#: jti values already used (single-use tokens). In-process is correct
#: while session affinity pins a call to one instance; Redis-backed
#: replay protection arrives with the persistence milestone.
_used_jtis: dict[str, float] = {}

#: naive per-IP limiter for invalid connection attempts
_invalid_attempts: dict[str, list[float]] = {}


def _too_many_invalid(client_ip: str) -> bool:
    now = time.monotonic()
    window = _invalid_attempts.setdefault(client_ip, [])
    window[:] = [t for t in window if t > now - INVALID_CONNECTION_WINDOW_SECONDS]
    if len(window) >= INVALID_CONNECTION_LIMIT:
        return True
    window.append(now)
    return False


def _jti_already_used(jti: str) -> bool:
    now = time.monotonic()
    # prune old entries
    for key in [k for k, t in _used_jtis.items() if t < now - 3600]:
        _used_jtis.pop(key, None)
    if jti in _used_jtis:
        return True
    _used_jtis[jti] = now
    return False


def reset_ws_state() -> None:
    """Test hook."""
    _used_jtis.clear()
    _invalid_attempts.clear()


@dataclass
class AudioBridge:
    """Bounded audio plumbing between Twilio and the pipeline."""

    websocket: WebSocket
    stream_sid: str | None = None
    inbound: asyncio.Queue[bytes] = field(
        default_factory=lambda: asyncio.Queue(maxsize=AUDIO_QUEUE_MAX_FRAMES)
    )
    frames_received: int = 0
    frames_dropped: int = 0
    last_sequence: int = 0
    duplicate_frames: int = 0

    def push_inbound(self, payload_b64: str, sequence: int) -> None:
        # Duplicate/replayed events are ignored; gaps are logged once.
        if sequence <= self.last_sequence and self.last_sequence != 0:
            self.duplicate_frames += 1
            return
        if self.last_sequence and sequence > self.last_sequence + 1:
            logger.debug("media_sequence_gap", expected=self.last_sequence + 1, got=sequence)
        self.last_sequence = sequence
        frame = base64.b64decode(payload_b64)
        self.frames_received += 1
        try:
            self.inbound.put_nowait(frame)
        except asyncio.QueueFull:
            # Backpressure: drop the oldest frame — live audio must not
            # buffer unboundedly, and stale audio is worthless.
            import contextlib

            with contextlib.suppress(asyncio.QueueEmpty):  # race guard
                self.inbound.get_nowait()
            self.inbound.put_nowait(frame)
            self.frames_dropped += 1

    async def send_audio(self, audio: bytes) -> None:
        if self.stream_sid is None:
            return
        await self.websocket.send_text(
            json.dumps(
                {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": base64.b64encode(audio).decode()},
                }
            )
        )

    async def clear_audio(self) -> None:
        """Barge-in: flush Twilio's playback buffer immediately."""
        if self.stream_sid is None:
            return
        await self.websocket.send_text(json.dumps({"event": "clear", "streamSid": self.stream_sid}))

    async def send_mark(self, name: str) -> None:
        if self.stream_sid is None:
            return
        await self.websocket.send_text(
            json.dumps(
                {
                    "event": "mark",
                    "streamSid": self.stream_sid,
                    "mark": {"name": name},
                }
            )
        )


@dataclass
class CallContext:
    """Trusted, server-side call identity (never from stream metadata)."""

    call_id: str
    call_sid: str
    tenant_id: str


async def resolve_call_context(call_sid: str, tenant_id: str) -> CallContext | None:
    """Re-resolve the call from the database. The token's claims are the
    lookup keys; the database row is the authority."""
    from sqlalchemy import select

    from voice.db import get_session_factory

    factory = get_session_factory()
    if factory is None:
        # No database configured (unit tests): trust the verified token.
        return CallContext(call_id=call_sid, call_sid=call_sid, tenant_id=tenant_id)
    from ai_database.models import Call

    async with factory() as session:
        call = (
            await session.execute(select(Call).where(Call.provider_call_sid == call_sid))
        ).scalar_one_or_none()
    if call is None or str(call.tenant_id) != tenant_id or call.ended_at is not None:
        return None
    return CallContext(call_id=str(call.id), call_sid=call_sid, tenant_id=tenant_id)


async def handle_media_stream(websocket: WebSocket, *, signing_key: str) -> None:
    """Accept, authenticate, and run one media-stream connection."""
    client_ip = websocket.client.host if websocket.client else "unknown"

    token = websocket.query_params.get("token", "")
    try:
        if _too_many_invalid(client_ip) and not token:
            raise CallTokenError("rate limited")
        claims = verify_call_token(token, signing_key=signing_key)
        if _jti_already_used(claims["jti"]):
            raise CallTokenError("token reuse")
    except CallTokenError as exc:
        _too_many_invalid(client_ip)
        logger.warning("media_ws_rejected", reason=str(exc))
        await websocket.close(code=4401)
        return

    context = await resolve_call_context(claims["call_sid"], claims["tenant_id"])
    if context is None:
        logger.warning("media_ws_no_call_record")
        await websocket.close(code=4404)
        return

    await websocket.accept()
    structlog.contextvars.bind_contextvars(call_id=context.call_id)
    bridge = AudioBridge(websocket=websocket)
    logger.info("media_ws_connected")

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS)
            except TimeoutError:
                logger.warning("media_ws_idle_timeout")
                break

            try:
                event: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("media_ws_malformed_event")
                continue

            kind = event.get("event")
            if kind == "connected":
                continue
            if kind == "start":
                start = event.get("start", {})
                # The start payload's CallSid must match the token; the
                # rest of its metadata is untrusted and unused.
                if start.get("callSid") not in (None, context.call_sid):
                    logger.warning("media_ws_callsid_mismatch")
                    break
                bridge.stream_sid = event.get("streamSid") or start.get("streamSid")
                logger.info("media_stream_started")
                continue
            if kind == "media":
                media = event.get("media", {})
                payload = media.get("payload")
                if payload:
                    bridge.push_inbound(
                        payload,
                        int(media.get("sequenceNumber") or event.get("sequenceNumber") or 0),
                    )
                continue
            if kind == "mark":
                continue
            if kind == "stop":
                logger.info(
                    "media_stream_stopped",
                    frames=bridge.frames_received,
                    dropped=bridge.frames_dropped,
                    duplicates=bridge.duplicate_frames,
                )
                break
    except WebSocketDisconnect:
        logger.info("media_ws_disconnected", frames=bridge.frames_received)
    finally:
        structlog.contextvars.unbind_contextvars("call_id")
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close()


def register_media_ws(app: FastAPI, *, signing_key_getter: "Callable[[], str | None]") -> None:
    """Attach the /ws endpoint to the FastAPI app."""

    @app.websocket("/ws")
    async def media_ws(websocket: WebSocket) -> None:
        signing_key = signing_key_getter()
        if not signing_key:
            await websocket.close(code=4500)
            return
        await handle_media_stream(websocket, signing_key=signing_key)
