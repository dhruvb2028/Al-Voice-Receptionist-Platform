"""Streaming speech-to-text provider interface (Deepgram in production)."""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from ai_providers.errors import ProviderUnavailableError


class TranscriptEvent(BaseModel):
    text: str
    is_final: bool
    confidence: float | None = None
    #: milliseconds from audio start
    start_ms: int | None = None
    end_ms: int | None = None
    #: provider-side finalization latency for telemetry
    finalization_ms: int | None = None


@runtime_checkable
class STTSession(Protocol):
    async def send_audio(self, chunk: bytes) -> None: ...

    def events(self) -> AsyncIterator[TranscriptEvent]:
        """Partial events stream continuously; a final event closes each
        utterance. Iteration ends when the session closes."""
        ...

    async def close(self) -> None: ...


@runtime_checkable
class STTProvider(Protocol):
    async def connect(self, *, sample_rate: int, encoding: str, language: str) -> STTSession: ...


class MockSTTSession:
    """Scripted STT session: feed expected utterances, receive
    partial→final event sequences as audio arrives."""

    def __init__(self, scripted_utterances: list[str]) -> None:
        self._scripted = list(scripted_utterances)
        self._pending: list[TranscriptEvent] = []
        self.closed = False
        self.audio_bytes_received = 0
        self.disconnect_after_chunks: int | None = None
        self._chunks_seen = 0

    async def send_audio(self, chunk: bytes) -> None:
        if self.closed:
            raise ProviderUnavailableError("session closed", provider="mock-stt")
        self._chunks_seen += 1
        if (
            self.disconnect_after_chunks is not None
            and self._chunks_seen > self.disconnect_after_chunks
        ):
            raise ProviderUnavailableError("stream dropped", provider="mock-stt")
        self.audio_bytes_received += len(chunk)
        if self._scripted:
            utterance = self._scripted.pop(0)
            words = utterance.split()
            partial = " ".join(words[: max(1, len(words) // 2)])
            self._pending.append(TranscriptEvent(text=partial, is_final=False, confidence=0.6))
            self._pending.append(
                TranscriptEvent(
                    text=utterance,
                    is_final=True,
                    confidence=0.95,
                    start_ms=0,
                    end_ms=1200,
                    finalization_ms=180,
                )
            )

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while not self.closed or self._pending:
            if self._pending:
                yield self._pending.pop(0)
            else:
                return

    async def close(self) -> None:
        self.closed = True


class MockSTTProvider:
    def __init__(self, scripted_utterances: list[str] | None = None) -> None:
        self.scripted_utterances = scripted_utterances or []
        self.sessions: list[MockSTTSession] = []

    async def connect(self, *, sample_rate: int, encoding: str, language: str) -> MockSTTSession:
        session = MockSTTSession(self.scripted_utterances)
        self.sessions.append(session)
        return session
