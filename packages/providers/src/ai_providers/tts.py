"""Streaming text-to-speech provider interface (Cartesia in production)."""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class TTSChunk(BaseModel):
    audio: bytes
    #: set on the first chunk only — time-to-first-byte for telemetry
    first_byte_ms: int | None = None


@runtime_checkable
class TTSSession(Protocol):
    async def send_text(self, text: str) -> None: ...

    def chunks(self) -> AsyncIterator[TTSChunk]: ...

    async def cancel(self) -> None:
        """Stop synthesis immediately (barge-in). Pending chunks are
        dropped, not flushed."""
        ...

    async def close(self) -> None: ...


@runtime_checkable
class TTSProvider(Protocol):
    async def open_session(
        self, *, voice_id: str, sample_rate: int, encoding: str
    ) -> TTSSession: ...


class MockTTSSession:
    def __init__(self, voice_id: str) -> None:
        self.voice_id = voice_id
        self.texts: list[str] = []
        self.cancelled = False
        self.closed = False
        self._pending: list[TTSChunk] = []
        self._first = True

    async def send_text(self, text: str) -> None:
        if self.cancelled or self.closed:
            return
        self.texts.append(text)
        # One deterministic chunk per ~10 chars of text.
        for offset in range(0, max(1, len(text)), 10):
            chunk = TTSChunk(
                audio=text[offset : offset + 10].encode("utf-8"),
                first_byte_ms=35 if self._first else None,
            )
            self._first = False
            self._pending.append(chunk)

    async def chunks(self) -> AsyncIterator[TTSChunk]:
        while self._pending and not self.cancelled:
            yield self._pending.pop(0)

    async def cancel(self) -> None:
        self.cancelled = True
        self._pending.clear()

    async def close(self) -> None:
        self.closed = True


class MockTTSProvider:
    def __init__(self) -> None:
        self.sessions: list[MockTTSSession] = []

    async def open_session(
        self, *, voice_id: str, sample_rate: int, encoding: str
    ) -> MockTTSSession:
        session = MockTTSSession(voice_id)
        self.sessions.append(session)
        return session
