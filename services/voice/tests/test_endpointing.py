"""Endpointing scenario tests with a deterministic clock.

Covers the nine required scenarios: short answer, hesitant answer, long
pause, caller correction, background noise, one-word response, rambling
explanation, silence, and caller speaking over the agent.
"""

from ai_providers.stt import TranscriptEvent
from voice.endpointing import EndpointingConfig, EndpointingEngine

VOICE_FRAME = bytes([0x30] * 160)  # clearly voiced µ-law frame
SILENCE_FRAME = bytes([0x7F] * 160)  # µ-law silence
NOISE_FRAME = bytes([0x30] * 12 + [0x7F] * 148)  # low-energy background


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _engine(**config_overrides: int) -> tuple[EndpointingEngine, FakeClock]:
    clock = FakeClock()
    engine = EndpointingEngine(config=EndpointingConfig(**config_overrides), clock=clock)
    return engine, clock


def _final(text: str) -> TranscriptEvent:
    return TranscriptEvent(text=text, is_final=True, confidence=0.95, finalization_ms=120)


def _interim(text: str) -> TranscriptEvent:
    return TranscriptEvent(text=text, is_final=False, confidence=0.6)


def test_short_answer_endpoints_after_minimum_silence() -> None:
    engine, clock = _engine()
    engine.feed_audio(VOICE_FRAME)
    engine.feed_transcript(_final("Tomorrow morning works."))
    clock.advance(0.2)
    assert engine.should_endpoint() is False  # under minimum silence
    clock.advance(0.3)  # 500ms total silence
    assert engine.should_endpoint() is True
    assert engine.transcript == "Tomorrow morning works."
    assert engine.metrics.endpoint_duration_ms is not None


def test_hesitant_answer_gets_short_pause_protection() -> None:
    engine, clock = _engine()
    engine.feed_audio(VOICE_FRAME)
    engine.feed_transcript(_final("I think it's the, um"))
    clock.advance(0.6)  # past minimum, but trailing hesitation
    assert engine.should_endpoint() is False
    # Caller finishes the thought.
    engine.feed_audio(VOICE_FRAME)
    engine.feed_transcript(_final("the kitchen sink."))
    clock.advance(0.6)
    assert engine.should_endpoint() is True
    assert "kitchen sink" in engine.transcript


def test_long_pause_eventually_endpoints() -> None:
    engine, clock = _engine()
    engine.feed_audio(VOICE_FRAME)
    engine.feed_transcript(_interim("so the problem is"))  # never finalized
    clock.advance(2.6)  # beyond maximum_silence_ms
    assert engine.should_endpoint() is True


def test_caller_correction_reopens_turn() -> None:
    engine, clock = _engine()
    engine.feed_audio(VOICE_FRAME)
    engine.feed_transcript(_final("Make it Tuesday."))
    clock.advance(0.3)
    # Caller corrects themselves before we endpointed.
    engine.feed_audio(VOICE_FRAME)
    engine.feed_transcript(_final("Actually, Wednesday is better."))
    assert engine.metrics.reopened_turns == 1
    clock.advance(0.6)
    assert engine.should_endpoint() is True
    assert "Wednesday" in engine.transcript


def test_background_noise_not_treated_as_voice() -> None:
    engine, clock = _engine()
    for _ in range(5):
        engine.feed_audio(NOISE_FRAME)
    clock.advance(3.0)
    # Noise alone never starts a turn.
    assert engine.should_endpoint() is False
    assert engine.metrics.background_noise_events > 0
    assert engine.metrics.voice_start_ms is None


def test_one_word_response_endpoints_quickly() -> None:
    engine, clock = _engine()
    engine.feed_audio(VOICE_FRAME)
    engine.feed_transcript(_final("Yes"))
    clock.advance(0.45)
    assert engine.should_endpoint() is True
    assert engine.transcript == "Yes"


def test_rambling_hits_maximum_turn_duration() -> None:
    engine, clock = _engine(maximum_turn_ms=5000)
    engine.feed_audio(VOICE_FRAME)
    for i in range(10):
        engine.feed_transcript(_interim(f"and then part {i}"))
        engine.feed_audio(VOICE_FRAME)
        clock.advance(0.6)
    # Still talking (voice 0.6s ago, under min silence) but turn cap hit.
    assert engine.should_endpoint() is True


def test_pure_silence_never_endpoints() -> None:
    engine, clock = _engine()
    for _ in range(10):
        engine.feed_audio(SILENCE_FRAME)
        clock.advance(0.5)
    assert engine.should_endpoint() is False
    assert engine.metrics.voice_start_ms is None


def test_talk_over_keeps_collecting() -> None:
    """Caller speaking over the agent: continuous voice defers endpointing
    until they actually stop."""
    engine, clock = _engine()
    for _ in range(6):
        engine.feed_audio(VOICE_FRAME)
        clock.advance(0.2)
    engine.feed_transcript(_final("wait wait I need to add something."))
    assert engine.should_endpoint() is False  # still within minimum silence
    clock.advance(0.5)
    assert engine.should_endpoint() is True


def test_voice_start_metric_recorded() -> None:
    engine, clock = _engine()
    engine.feed_audio(SILENCE_FRAME)
    clock.advance(0.8)
    engine.feed_audio(VOICE_FRAME)
    assert engine.metrics.voice_start_ms is not None
    # float→int truncation may shave a millisecond
    assert engine.metrics.voice_start_ms >= 799


def test_reset_for_next_turn_keeps_config_and_clock() -> None:
    engine, clock = _engine(minimum_silence_ms=999)
    engine.feed_audio(VOICE_FRAME)
    next_engine = engine.reset_for_next_turn()
    assert next_engine.config.minimum_silence_ms == 999
    assert next_engine.clock is engine.clock
    assert next_engine.transcript == ""
