"""Deepgram event-parsing tests (no network)."""

from ai_providers.deepgram import DeepgramSTTSession


def _session() -> DeepgramSTTSession:
    return DeepgramSTTSession(
        api_key="dg-test",
        sample_rate=8000,
        encoding="mulaw",
        language="en",
    )


def test_parse_interim_result() -> None:
    event = _session()._parse_event(
        '{"type": "Results", "is_final": false, "speech_final": false,'
        ' "channel": {"alternatives": [{"transcript": "hello I", "confidence": 0.62}]}}'
    )
    assert event is not None
    assert event.text == "hello I"
    assert event.is_final is False
    assert event.confidence == 0.62


def test_parse_final_with_word_timing() -> None:
    event = _session()._parse_event(
        '{"type": "Results", "is_final": true, "speech_final": true, "duration": 1.42,'
        ' "channel": {"alternatives": [{"transcript": "hello, I need a plumber.",'
        ' "confidence": 0.97,'
        ' "words": [{"word": "hello", "start": 0.1, "end": 0.4},'
        ' {"word": "plumber", "start": 1.1, "end": 1.5}]}]}}'
    )
    assert event is not None
    assert event.is_final is True
    assert event.text.endswith("plumber.")  # punctuation preserved
    assert event.start_ms == 100
    assert event.end_ms == 1500
    assert event.finalization_ms == 1420


def test_parse_utterance_end_is_final_marker() -> None:
    event = _session()._parse_event('{"type": "UtteranceEnd", "last_word_end": 2.1}')
    assert event is not None
    assert event.is_final is True
    assert event.text == ""


def test_parse_ignores_metadata_and_empty_interims() -> None:
    session = _session()
    assert session._parse_event('{"type": "Metadata", "request_id": "x"}') is None
    assert (
        session._parse_event(
            '{"type": "Results", "is_final": false,'
            ' "channel": {"alternatives": [{"transcript": ""}]}}'
        )
        is None
    )


def test_parse_malformed_event_returns_none() -> None:
    assert _session()._parse_event("not json{") is None


def test_language_and_audio_params_configured() -> None:
    session = DeepgramSTTSession(
        api_key="dg-test", sample_rate=8000, encoding="mulaw", language="en"
    )
    assert session._params["encoding"] == "mulaw"
    assert session._params["sample_rate"] == "8000"
    assert session._params["language"] == "en"
    assert session._params["punctuate"] == "true"
    assert session._params["interim_results"] == "true"
