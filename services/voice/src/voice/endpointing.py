"""End-of-turn detection.

Fixed silence alone endpoints too early on hesitant callers and too
late on quick ones. The engine fuses several signals, all thresholds
configurable:

- voice activity (audio energy above a floor)
- Deepgram finalization signals (``speech_final`` / UtteranceEnd)
- punctuation on the accumulated transcript
- semantic completeness (does the text look like a finished thought?)
- minimum silence (never endpoint mid-breath)
- short-pause protection (hesitation markers extend the window)
- maximum silence (always endpoint eventually)
- maximum turn duration (ramblers get a response too)

Every decision records metrics so endpointing quality is measurable
per call, not anecdotal.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog
from ai_providers.stt import TranscriptEvent

logger = structlog.get_logger()

#: µ-law byte values near the zero-point (silence) — cheap VAD floor.
_MULAW_SILENCE_BYTES = frozenset(range(0x7A, 0x80)) | frozenset(range(0xFA, 0x100))

_HESITATION_RE = re.compile(
    r"(?:\b(?:um+|uh+|er+|hmm+|so|and|but|because|well)\s*)$|,\s*$", re.IGNORECASE
)
_TERMINAL_PUNCTUATION_RE = re.compile(r"[.!?]\s*$")
_QUESTION_WORDS = ("what", "when", "where", "who", "how", "why", "can", "could", "do", "is")


@dataclass
class EndpointingConfig:
    """Tunable thresholds (milliseconds unless noted)."""

    minimum_silence_ms: int = 400
    maximum_silence_ms: int = 2500
    short_pause_extension_ms: int = 700
    maximum_turn_ms: int = 30_000
    #: fraction of bytes in a frame that must be non-silent to count as voice
    vad_voice_ratio: float = 0.1


@dataclass
class EndpointingMetrics:
    voice_start_ms: int | None = None
    end_of_speech_ms: int | None = None
    endpoint_duration_ms: int | None = None
    stt_finalization_ms: int | None = None
    false_endpoint_candidates: int = 0
    reopened_turns: int = 0
    background_noise_events: int = 0


@dataclass
class EndpointingEngine:
    """Feed audio frames and transcript events; poll ``should_endpoint``."""

    config: EndpointingConfig = field(default_factory=EndpointingConfig)
    metrics: EndpointingMetrics = field(default_factory=EndpointingMetrics)
    #: injectable for deterministic tests
    clock: Callable[[], float] = time.monotonic

    _turn_started: float = 0.0
    _last_voice_at: float | None = None
    _voice_seen: bool = False
    _transcript: str = ""
    _speech_final_seen: bool = False
    _endpoint_candidate_at: float | None = None

    # -- signal ingestion ----------------------------------------------------

    def feed_audio(self, frame: bytes) -> None:
        """µ-law frame → VAD update."""
        if not frame:
            return
        if self._turn_started == 0.0:
            self._turn_started = self.clock()
        voiced = sum(1 for byte in frame if byte not in _MULAW_SILENCE_BYTES)
        ratio = voiced / len(frame)
        now = self.clock()
        if ratio >= self.config.vad_voice_ratio:
            if not self._voice_seen:
                self._voice_seen = True
                self.metrics.voice_start_ms = int((now - self._turn_started) * 1000)
            elif self._endpoint_candidate_at is not None and not self._speech_final_seen:
                # Voice resumed after we considered endpointing — the
                # candidate was false; the turn reopens.
                self.metrics.false_endpoint_candidates += 1
                self.metrics.reopened_turns += 1
                self._endpoint_candidate_at = None
            self._last_voice_at = now
        elif not self._voice_seen and ratio > self.config.vad_voice_ratio / 2:
            # audible energy before any speech — background noise
            self.metrics.background_noise_events += 1

    def feed_transcript(self, event: TranscriptEvent) -> None:
        if event.text:
            if self._speech_final_seen and event.is_final:
                # New finalized speech after a previous endpoint signal —
                # the caller kept going (e.g. a correction).
                self.metrics.reopened_turns += 1
                self._speech_final_seen = False
            self._transcript = (
                f"{self._transcript} {event.text}".strip() if self._transcript else event.text
            )
        if event.is_final:
            self._speech_final_seen = True
            if event.finalization_ms is not None:
                self.metrics.stt_finalization_ms = event.finalization_ms

    # -- semantic heuristics -------------------------------------------------

    def _looks_complete(self) -> bool:
        text = self._transcript.strip()
        if not text:
            return False
        if _HESITATION_RE.search(text):
            return False  # trailing "um", "so", "and", or a comma
        if _TERMINAL_PUNCTUATION_RE.search(text):
            return True
        # Very short answers ("yes", "the kitchen") are complete once
        # Deepgram finalizes them.
        word_count = len(text.split())
        if word_count <= 4 and self._speech_final_seen:
            return True
        return self._speech_final_seen

    # -- decision ------------------------------------------------------------

    def should_endpoint(self) -> bool:
        now = self.clock()
        if self._turn_started == 0.0:
            self._turn_started = now

        # Maximum-turn override: ramblers get a response.
        if (now - self._turn_started) * 1000 >= self.config.maximum_turn_ms and self._voice_seen:
            logger.debug("endpoint_max_turn")
            return self._finalize(now)

        if not self._voice_seen or self._last_voice_at is None:
            return False  # silence-only turns never endpoint (caller said nothing)

        silence_ms = (now - self._last_voice_at) * 1000

        if silence_ms < self.config.minimum_silence_ms:
            return False

        # Maximum-silence override: endpoint regardless of semantics.
        if silence_ms >= self.config.maximum_silence_ms:
            return self._finalize(now)

        required_ms = float(self.config.minimum_silence_ms)
        if not self._looks_complete():
            # Short-pause protection: hesitant/incomplete speech extends
            # the window instead of cutting the caller off.
            required_ms += self.config.short_pause_extension_ms
            if self._endpoint_candidate_at is None:
                self._endpoint_candidate_at = now

        if silence_ms >= required_ms and (self._speech_final_seen or self._looks_complete()):
            return self._finalize(now)
        return False

    def _finalize(self, now: float) -> bool:
        if self._last_voice_at is not None:
            self.metrics.end_of_speech_ms = int((self._last_voice_at - self._turn_started) * 1000)
            self.metrics.endpoint_duration_ms = int((now - self._last_voice_at) * 1000)
        return True

    # -- turn output ---------------------------------------------------------

    @property
    def transcript(self) -> str:
        return self._transcript.strip()

    def reset_for_next_turn(self) -> "EndpointingEngine":
        return EndpointingEngine(config=self.config, clock=self.clock)
