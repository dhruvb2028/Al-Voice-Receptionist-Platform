"""Speech-out tests: chunk splitting, incremental playback, barge-in,
filler rules, and generated-vs-played bookkeeping."""

from ai_providers.tts import MockTTSProvider
from voice.speech_out import (
    FillerPolicy,
    PlaybackRecord,
    SpeechController,
    split_speakable,
)


class Sink:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.cleared = 0
        self.llm_cancelled = 0

    async def send(self, audio: bytes) -> None:
        self.sent.append(audio)

    async def clear(self) -> None:
        self.cleared += 1

    async def cancel_llm(self) -> None:
        self.llm_cancelled += 1


async def _controller() -> tuple[SpeechController, Sink]:
    provider = MockTTSProvider()
    session = await provider.open_session(voice_id="warm-1", sample_rate=8000, encoding="mulaw")
    sink = Sink()
    controller = SpeechController(
        tts=session,
        send_audio=sink.send,
        clear_audio=sink.clear,
        cancel_llm=sink.cancel_llm,
    )
    return controller, sink


# --- splitting ---------------------------------------------------------------


def test_split_releases_complete_sentences_only() -> None:
    chunks, rest = split_speakable("I can help with that. What's your")
    assert chunks == ["I can help with that."]
    assert rest == "What's your"


def test_split_force_flushes_tail() -> None:
    chunks, rest = split_speakable("What's your address?", force=True)
    assert chunks == ["What's your address?"]
    assert rest == ""


def test_split_holds_short_fragments_until_long_enough() -> None:
    chunks, rest = split_speakable("Sure. Ok. That works well for us today. And")
    # Short pieces merge until they pass the minimum chunk size.
    assert chunks
    assert all(len(c) >= 10 for c in chunks)
    assert rest.endswith("And")


def test_split_empty() -> None:
    assert split_speakable("") == ([], "")


# --- incremental playback ----------------------------------------------------


async def test_streams_playback_before_response_completes() -> None:
    controller, sink = await _controller()
    await controller.feed_text("Our next opening is Tuesday. ")
    # First sentence already played while the response continues.
    assert sink.sent, "audio should flow before finish()"
    played_before_finish = len(sink.sent)
    await controller.feed_text("Would that work for you?")
    await controller.finish()
    assert len(sink.sent) > played_before_finish
    assert controller.record.first_playback_ms is not None
    assert controller.record.generated_text.startswith("Our next opening")
    assert controller.record.played_text.startswith("Our next opening")
    assert controller.record.bytes_played == sum(len(b) for b in sink.sent)


# --- barge-in ----------------------------------------------------------------


async def test_barge_in_stops_everything_and_marks_turn() -> None:
    controller, sink = await _controller()
    await controller.feed_text("Let me walk you through all of our services. ")
    assert sink.sent

    await controller.barge_in()
    assert controller.record.interrupted is True
    assert controller.record.barge_in_stop_ms is not None
    assert sink.cleared == 1
    assert sink.llm_cancelled == 1

    # Nothing plays after a barge-in.
    before = len(sink.sent)
    await controller.feed_text("This text arrives too late.")
    await controller.finish()
    assert len(sink.sent) == before
    assert "too late" not in controller.record.played_text


async def test_heard_estimate_truncates_to_played_audio() -> None:
    record = PlaybackRecord(
        spoken_chunks=["We can come Tuesday at ten or Wednesday at two in the afternoon."],
        bytes_played=8000,  # exactly one second ≈ 15 chars
        interrupted=True,
    )
    heard = record.heard_estimate()
    assert heard.startswith("We can")
    assert len(heard) < len(record.played_text)


async def test_uninterrupted_heard_equals_played() -> None:
    record = PlaybackRecord(spoken_chunks=["Short reply."], bytes_played=999999)
    assert record.heard_estimate() == "Short reply."


# --- filler ------------------------------------------------------------------


async def test_filler_rotates_and_respects_cap() -> None:
    policy = FillerPolicy(
        phrases=["Let me check that for you.", "One moment while I look at the calendar."],
        max_uses_per_call=3,
    )
    assert policy.next_phrase() == "Let me check that for you."
    assert policy.next_phrase() == "One moment while I look at the calendar."
    assert policy.next_phrase() == "Let me check that for you."  # rotation, no repeat
    assert policy.next_phrase() is None  # cap reached


async def test_filler_disabled_by_tenant() -> None:
    policy = FillerPolicy(phrases=["Let me check."], enabled=False)
    assert policy.next_phrase() is None


async def test_filler_never_overlaps_real_speech() -> None:
    controller, sink = await _controller()
    await controller.feed_text("Here is the actual answer to your question. ")
    policy = FillerPolicy(phrases=["Let me check that for you."])
    spoke = await controller.speak_filler(policy)
    assert spoke is False  # real speech already underway
    assert "Let me check" not in controller.record.played_text


async def test_filler_speaks_and_is_cancellable() -> None:
    controller, sink = await _controller()
    policy = FillerPolicy(phrases=["Let me check that for you."])
    spoke = await controller.speak_filler(policy)
    assert spoke is True
    assert "Let me check that for you." in controller.record.played_text
    await controller.barge_in()
    assert controller.cancelled
