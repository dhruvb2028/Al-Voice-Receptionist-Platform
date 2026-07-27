"""Outbound speech control: chunking, playback tracking, barge-in, and
filler phrases.

The controller sits between the conversation engine and the TTS/audio
bridge. Text streams in (LLM deltas), is split at safe spoken
boundaries, synthesized incrementally, and played before the full
response exists. On barge-in everything stops within a frame: TTS
cancelled, queued audio cleared, and the turn record keeps only what
the caller plausibly heard.
"""

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
from ai_providers.tts import TTSSession

logger = structlog.get_logger()

#: sentence-ish boundaries safe to hand to TTS independently
_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|(?<=[;:])\s+|(?<=,)\s+(?=\w{4,})")
MIN_CHUNK_CHARS = 16
#: µ-law 8 kHz — one byte per sample
BYTES_PER_SECOND = 8000


def split_speakable(text: str, *, force: bool = False) -> tuple[list[str], str]:
    """Split accumulated text into speakable chunks plus a remainder.

    Only complete boundary-terminated chunks are released unless
    ``force`` (end of response) flushes the tail too.
    """
    if not text:
        return [], ""
    pieces = _BOUNDARY_RE.split(text)
    if not pieces:
        return ([], text) if not force else ([text], "")
    chunks: list[str] = []
    buffer = ""
    for piece in pieces[:-1]:
        buffer = f"{buffer} {piece}".strip() if buffer else piece
        if len(buffer) >= MIN_CHUNK_CHARS:
            chunks.append(buffer)
            buffer = ""
    tail = f"{buffer} {pieces[-1]}".strip() if buffer else pieces[-1]
    if force and tail:
        chunks.append(tail)
        tail = ""
    return chunks, tail


@dataclass
class PlaybackRecord:
    """What was generated vs. what the caller actually heard."""

    generated_text: str = ""
    spoken_chunks: list[str] = field(default_factory=list)
    bytes_played: int = 0
    interrupted: bool = False
    barge_in_stop_ms: int | None = None
    first_playback_ms: int | None = None

    @property
    def played_text(self) -> str:
        return " ".join(self.spoken_chunks)

    def heard_estimate(self) -> str:
        """Text the caller plausibly heard: fully played chunks, plus a
        proportional slice of the chunk in flight when interrupted."""
        if not self.interrupted or not self.spoken_chunks:
            return self.played_text
        played_seconds = self.bytes_played / BYTES_PER_SECOND
        # ~15 chars/second of speech is a serviceable estimate at 8 kHz.
        heard_chars = int(played_seconds * 15)
        text = self.played_text
        return text[:heard_chars].rsplit(" ", 1)[0] if heard_chars < len(text) else text


@dataclass
class FillerPolicy:
    """Tenant-approved filler phrases for slow tools."""

    phrases: list[str] = field(default_factory=list)
    enabled: bool = True
    #: never speak filler twice in a row within one call
    max_uses_per_call: int = 3

    _uses: int = 0
    _last_index: int = -1

    def next_phrase(self) -> str | None:
        if not self.enabled or not self.phrases or self._uses >= self.max_uses_per_call:
            return None
        # Rotate; never repeat the previous phrase back-to-back.
        index = (self._last_index + 1) % len(self.phrases)
        self._last_index = index
        self._uses += 1
        return self.phrases[index]


class SpeechController:
    """Drives one response's speech: stream text in, audio out."""

    def __init__(
        self,
        *,
        tts: TTSSession,
        send_audio: Callable[[bytes], Awaitable[None]],
        clear_audio: Callable[[], Awaitable[None]],
        cancel_llm: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._tts = tts
        self._send_audio = send_audio
        self._clear_audio = clear_audio
        self._cancel_llm = cancel_llm
        self._clock = clock
        self.record = PlaybackRecord()
        self._pending_text = ""
        self._started_at = self._clock()
        self._cancelled = False
        self._filler_active = False

    # -- text ingestion ------------------------------------------------------

    async def feed_text(self, delta: str) -> None:
        """LLM text delta → speak complete chunks as they form."""
        if self._cancelled:
            return
        self.record.generated_text += delta
        self._pending_text += delta
        chunks, self._pending_text = split_speakable(self._pending_text)
        for chunk in chunks:
            await self._speak_chunk(chunk)

    async def finish(self) -> None:
        """End of response: flush the remainder and drain audio."""
        if self._cancelled:
            return
        chunks, self._pending_text = split_speakable(self._pending_text, force=True)
        for chunk in chunks:
            await self._speak_chunk(chunk)
        flush = getattr(self._tts, "flush", None)
        if flush is not None:
            await flush()
        await self._drain_audio()

    async def _speak_chunk(self, chunk: str) -> None:
        if self._cancelled or not chunk.strip():
            return
        self.record.spoken_chunks.append(chunk)
        await self._tts.send_text(chunk)
        await self._drain_audio()

    async def _drain_audio(self) -> None:
        async for tts_chunk in self._tts.chunks():
            if self._cancelled:
                return
            if self.record.first_playback_ms is None:
                self.record.first_playback_ms = int((self._clock() - self._started_at) * 1000)
            await self._send_audio(tts_chunk.audio)
            self.record.bytes_played += len(tts_chunk.audio)

    # -- filler --------------------------------------------------------------

    async def speak_filler(self, policy: FillerPolicy) -> bool:
        """Speak one approved filler phrase (cancellable like any speech).

        Never runs while real speech is pending, so filler cannot overlap
        the final answer."""
        if self._cancelled or self._pending_text.strip() or self.record.spoken_chunks:
            return False
        phrase = policy.next_phrase()
        if phrase is None:
            return False
        self._filler_active = True
        try:
            await self._speak_chunk(phrase)
        finally:
            self._filler_active = False
        return True

    # -- barge-in ------------------------------------------------------------

    async def barge_in(self) -> None:
        """Caller started speaking: stop everything, now."""
        if self._cancelled:
            return
        started = self._clock()
        self._cancelled = True
        await self._tts.cancel()
        if self._cancel_llm is not None:
            await self._cancel_llm()
        await self._clear_audio()
        self.record.interrupted = True
        self.record.barge_in_stop_ms = int((self._clock() - started) * 1000)
        logger.info(
            "barge_in",
            stop_ms=self.record.barge_in_stop_ms,
            bytes_played=self.record.bytes_played,
        )

    @property
    def cancelled(self) -> bool:
        return self._cancelled
