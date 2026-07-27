"""Call recorder tests: event capture, per-turn metrics, summary
computation, containment, and cost estimation."""

import uuid

from voice.call_recorder import (
    CallEvent,
    CallRecorder,
    estimate_cost_cents,
)


def _recorder() -> CallRecorder:
    return CallRecorder(
        tenant_id=str(uuid.uuid4()), call_id=str(uuid.uuid4()), session_factory=None
    )


def test_events_are_buffered_in_order() -> None:
    recorder = _recorder()
    recorder.record_event(CallEvent.CALL_STARTED)
    recorder.record_event(CallEvent.GREETING_PLAYED)
    recorder.record_event(CallEvent.CALLER_SPEECH_BEGAN, turn_index=1)
    recorder.record_event(CallEvent.FINAL_TRANSCRIPT, turn_index=1)
    names = [event.value for event, _, _ in recorder.events]
    assert names == [
        "call_started",
        "greeting_played",
        "caller_speech_began",
        "final_transcript",
    ]


def test_all_required_events_exist() -> None:
    required = {
        "call_started",
        "greeting_played",
        "caller_speech_began",
        "partial_transcript",
        "final_transcript",
        "caller_turn_ended",
        "agent_generation_began",
        "agent_first_token",
        "tool_call_started",
        "tool_call_completed",
        "tts_began",
        "first_audio_played",
        "agent_interrupted",
        "transfer_initiated",
        "transfer_connected",
        "message_created",
        "call_ended",
        "call_failed",
    }
    assert {event.value for event in CallEvent} == required


def test_turn_metrics_accumulate_by_index() -> None:
    recorder = _recorder()
    recorder.record_turn_metrics(1, vad_start_ms=120, endpointing_ms=450)
    recorder.record_turn_metrics(1, llm_first_token_ms=180, total_latency_ms=850)
    recorder.record_turn_metrics(2, vad_start_ms=90)
    assert len(recorder.turns) == 2
    first = recorder.turns[0]
    assert first.vad_start_ms == 120
    assert first.endpointing_ms == 450
    assert first.llm_first_token_ms == 180
    assert first.total_latency_ms == 850


def test_interrupt_event_marks_turn() -> None:
    recorder = _recorder()
    recorder.record_event(CallEvent.AGENT_INTERRUPTED, turn_index=3, duration_ms=95)
    metrics = recorder.turns[0]
    assert metrics.turn_index == 3
    assert metrics.interrupted is True
    assert metrics.barge_in_stop_ms == 95


async def test_summary_contained_call() -> None:
    recorder = _recorder()
    recorder.record_turn_metrics(1, total_latency_ms=700)
    recorder.record_turn_metrics(2, total_latency_ms=800)
    recorder.add_usage("stt_seconds", 60)
    recorder.add_usage("llm_tokens", 2000)
    recorder.add_usage("tts_characters", 900)

    summary = await recorder.finalize(outcome="booked", booked=True)
    assert summary.turn_count == 2
    assert summary.booked is True
    assert summary.contained is True
    assert summary.duration_seconds is not None
    assert summary.provider_usage["telephony_seconds"] == summary.duration_seconds
    assert summary.estimated_cost_cents == estimate_cost_cents(summary.provider_usage)


async def test_summary_escalated_call_not_contained() -> None:
    recorder = _recorder()
    recorder.record_event(CallEvent.TRANSFER_INITIATED)
    summary = await recorder.finalize(outcome="transferred")
    assert summary.escalated is True
    assert summary.contained is False


async def test_summary_failed_call_carries_error_category() -> None:
    recorder = _recorder()
    recorder.record_event(CallEvent.CALL_FAILED, error_category="stt_unavailable")
    summary = await recorder.finalize(outcome="failed")
    assert summary.error_category == "stt_unavailable"
    assert summary.contained is False


async def test_message_event_reflected_in_summary() -> None:
    recorder = _recorder()
    recorder.record_event(CallEvent.MESSAGE_CREATED)
    summary = await recorder.finalize(outcome="message_taken")
    assert summary.message_taken is True
    assert summary.contained is True


def test_cost_estimate_covers_all_usage_kinds() -> None:
    usage = {
        "stt_seconds": 120,
        "llm_tokens": 5000,
        "tts_characters": 1500,
        "telephony_seconds": 120,
    }
    cost = estimate_cost_cents(usage)
    assert cost > 0
    # Unknown kinds contribute nothing rather than crashing.
    assert estimate_cost_cents({"quantum_flux": 999}) == 0
