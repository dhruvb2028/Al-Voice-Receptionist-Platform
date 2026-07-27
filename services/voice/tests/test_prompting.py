"""Prompt assembly tests: versioned files, tenant context, state summary."""

from ai_domain.config import ReceptionistConfig
from ai_domain.state_machine import (
    CallState,
    CallStateData,
    ConversationStateMachine,
)
from voice.prompting import build_state_summary, build_system_prompt, load_prompt


def _config() -> ReceptionistConfig:
    return ReceptionistConfig.model_validate(
        {
            "identity": {"business_name": "Harbor Plumbing", "timezone": "America/New_York"},
            "greeting": {"greeting": "Thanks for calling Harbor Plumbing!"},
            "services": [{"name": "Drain cleaning", "duration_minutes": 90}],
            "hours": [{"weekday": d, "opens_at": "08:00", "closes_at": "17:00"} for d in range(6)]
            + [{"weekday": 6, "closed": True}],
            "service_area": {"postal_codes": ["02101"]},
            "escalation": {"emergency_destination": "+15555550100"},
            "voice": {"voice_id": "warm-1", "speaking_style": "calm and friendly"},
        }
    )


def test_prompt_files_carry_version_metadata() -> None:
    _, base_meta = load_prompt("base")
    _, vertical_meta = load_prompt("home-services")
    assert base_meta["version"] == "1"
    assert vertical_meta["version"] == "1"


def test_system_prompt_fills_tenant_context() -> None:
    prompt, versions = build_system_prompt(_config())
    assert "Harbor Plumbing" in prompt
    assert "calm and friendly" in prompt
    # Safety rules present verbatim.
    assert "Never invent prices" in prompt
    # No unfilled placeholders remain.
    assert "{business_name}" not in prompt
    assert "{persona}" not in prompt
    assert versions == {"base_version": "1", "vertical_version": "1"}


def test_state_summary_reflects_machine() -> None:
    machine = ConversationStateMachine(CallStateData(tenant_id="t", call_id="c"))
    machine.state = CallState.CONFIRMING_BOOKING_DETAILS
    machine.data.set_field("caller_name", "Pat")
    machine.data.confirm_field("caller_name")
    machine.data.service_area_ok = True
    machine.data.presented_slots = ["Tue 10 AM", "Wed 2 PM"]
    machine.data.selected_slot = "Tue 10 AM"

    summary = build_state_summary(machine)
    assert "confirming_booking_details" in summary
    assert "caller_name=Pat" in summary
    assert "callback_number" in summary  # still unresolved
    assert "IN the service area" in summary
    assert "Tue 10 AM" in summary


def test_state_summary_out_of_area_directs_to_message() -> None:
    machine = ConversationStateMachine(CallStateData(tenant_id="t", call_id="c"))
    machine.data.service_area_ok = False
    summary = build_state_summary(machine)
    assert "OUTSIDE the service area" in summary
    assert "message" in summary
