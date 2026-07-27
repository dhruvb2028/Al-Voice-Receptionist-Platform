"""State-machine transition tests: every valid path, invalid paths,
booking gates, authoritative results, and mandatory overrides."""

from datetime import UTC, datetime, timedelta

import pytest
from ai_domain.state_machine import (
    _TRANSITIONS,
    TERMINAL_STATES,
    CallState,
    CallStateData,
    ConversationStateMachine,
    InvalidTransitionError,
)


def _state(machine: ConversationStateMachine) -> CallState:
    """Read the state without mypy literal-narrowing across method calls."""
    return machine.state


def _machine(**data_overrides: object) -> ConversationStateMachine:
    data = CallStateData(tenant_id="t1", call_id="c1", **data_overrides)  # type: ignore[arg-type]
    return ConversationStateMachine(data)


def _machine_at(state: CallState, **data_overrides: object) -> ConversationStateMachine:
    machine = _machine(**data_overrides)
    machine.state = state
    return machine


def _ready_for_booking() -> ConversationStateMachine:
    machine = _machine_at(CallState.CONFIRMING_BOOKING_DETAILS)
    for field, value in (
        ("caller_name", "Pat"),
        ("callback_number", "+15550001111"),
        ("address", "1 Main St"),
        ("service", "Drain cleaning"),
    ):
        machine.data.set_field(field, value)
        machine.data.confirm_field(field)
    machine.data.service_area_ok = True
    machine.data.selected_slot = "2026-08-01T10:00"
    return machine


# --- exhaustive table coverage ----------------------------------------------


ALL_STATES = list(CallState)


def test_every_state_present_in_table() -> None:
    assert set(_TRANSITIONS) == set(ALL_STATES)


def test_terminal_states_allow_nothing() -> None:
    for terminal in TERMINAL_STATES:
        assert _TRANSITIONS[terminal] == frozenset()


@pytest.mark.parametrize("state", ALL_STATES)
def test_valid_routine_transitions(state: CallState) -> None:
    """Every entry in the table is actually applicable (booking-gated
    targets receive the prerequisites)."""
    for target in _TRANSITIONS[state]:
        if target is CallState.BOOKED:
            continue  # only reachable via record_booking_success
        if target is CallState.BOOKING_IN_PROGRESS:
            machine = _ready_for_booking()
            machine.state = state
        else:
            machine = _machine_at(state)
        machine.transition(target)
        assert machine.state is target


@pytest.mark.parametrize("state", ALL_STATES)
def test_invalid_routine_transitions_rejected(state: CallState) -> None:
    """Everything absent from the table is rejected (override targets
    excepted — they are reachable from any non-terminal state)."""
    allowed = _TRANSITIONS[state]
    override_ok = (
        {CallState.TRANSFER_REQUESTED, CallState.TAKING_MESSAGE}
        if state not in TERMINAL_STATES
        else set()
    )
    for target in ALL_STATES:
        if target in allowed or target in override_ok:
            continue
        machine = _machine_at(state)
        with pytest.raises(InvalidTransitionError):
            machine.transition(target)


def test_full_happy_path_to_booked() -> None:
    machine = _machine()
    path = [
        CallState.GREETING,
        CallState.INTENT_DISCOVERY,
        CallState.SERVICE_IDENTIFICATION,
        CallState.URGENCY_ASSESSMENT,
        CallState.COLLECTING_NAME,
        CallState.COLLECTING_PHONE,
        CallState.COLLECTING_ADDRESS,
        CallState.CHECKING_SERVICE_AREA,
    ]
    for target in path:
        machine.transition(target)

    machine.record_service_area_result(True)
    assert _state(machine) is CallState.CHECKING_AVAILABILITY

    machine.record_slots_presented(["Tue 10 AM", "Wed 2 PM"])
    assert _state(machine) is CallState.PRESENTING_SLOTS

    machine.transition(CallState.CONFIRMING_BOOKING_DETAILS)
    for field, value in (
        ("caller_name", "Pat"),
        ("callback_number", "+15550001111"),
        ("address", "1 Main St"),
        ("service", "Drain cleaning"),
    ):
        machine.data.set_field(field, value)
        machine.data.confirm_field(field)
    machine.data.selected_slot = "Tue 10 AM"

    machine.transition(CallState.BOOKING_IN_PROGRESS)
    machine.record_booking_success(booking_id="b1")
    assert _state(machine) is CallState.BOOKED

    machine.transition(CallState.WRAPPING_UP)
    machine.transition(CallState.COMPLETED)
    assert _state(machine) in TERMINAL_STATES


# --- booking gates -----------------------------------------------------------


def test_booking_blocked_without_confirmed_fields() -> None:
    machine = _machine_at(CallState.CONFIRMING_BOOKING_DETAILS)
    machine.data.service_area_ok = True
    machine.data.selected_slot = "slot"
    ok, reason = machine.can_transition(CallState.BOOKING_IN_PROGRESS)
    assert not ok
    assert "unconfirmed fields" in reason


def test_booking_blocked_without_service_area() -> None:
    machine = _ready_for_booking()
    machine.data.service_area_ok = None
    ok, reason = machine.can_transition(CallState.BOOKING_IN_PROGRESS)
    assert not ok
    assert "service area" in reason


def test_booking_blocked_without_selected_slot() -> None:
    machine = _ready_for_booking()
    machine.data.selected_slot = None
    ok, reason = machine.can_transition(CallState.BOOKING_IN_PROGRESS)
    assert not ok
    assert "slot" in reason


def test_booked_unreachable_by_direct_transition() -> None:
    machine = _machine_at(CallState.BOOKING_IN_PROGRESS)
    with pytest.raises(InvalidTransitionError, match="record_booking_success"):
        machine.transition(CallState.BOOKED)


def test_booking_success_outside_progress_rejected() -> None:
    machine = _machine_at(CallState.PRESENTING_SLOTS)
    with pytest.raises(InvalidTransitionError):
        machine.record_booking_success(booking_id="b1")


def test_booking_failure_offers_alternatives() -> None:
    machine = _ready_for_booking()
    machine.transition(CallState.BOOKING_IN_PROGRESS)
    machine.record_booking_failure()
    assert machine.state is CallState.PRESENTING_SLOTS
    assert machine.data.booking_status == "failed"


# --- caller corrections ------------------------------------------------------


def test_correction_replaces_unconfirmed_value() -> None:
    machine = _machine()
    machine.data.set_field("address", "1 Main St")
    machine.data.set_field("address", "2 Oak Ave")
    assert machine.data.address == "2 Oak Ave"


def test_correction_reopens_confirmed_fact() -> None:
    machine = _machine()
    machine.data.set_field("address", "1 Main St")
    machine.data.confirm_field("address")
    assert "address" in machine.data.confirmed_facts
    machine.data.set_field("address", "2 Oak Ave")
    assert "address" not in machine.data.confirmed_facts
    assert "address" in machine.data.unresolved_fields()


def test_confirming_unset_field_rejected() -> None:
    machine = _machine()
    with pytest.raises(ValueError, match="unset field"):
        machine.data.confirm_field("caller_name")


# --- authoritative tool results ----------------------------------------------


def test_out_of_area_routes_to_message() -> None:
    machine = _machine_at(CallState.CHECKING_SERVICE_AREA)
    machine.record_service_area_result(False)
    assert machine.state is CallState.TAKING_MESSAGE
    assert machine.data.service_area_ok is False


def test_no_availability_routes_to_message() -> None:
    machine = _machine_at(CallState.CHECKING_AVAILABILITY)
    machine.record_slots_presented([])
    assert machine.state is CallState.TAKING_MESSAGE


def test_transfer_failure_falls_back_to_message() -> None:
    machine = _machine_at(CallState.TRANSFER_REQUESTED)
    machine.record_transfer_result(connected=False)
    assert machine.state is CallState.TAKING_MESSAGE
    assert machine.data.transfer_status == "failed"


def test_transfer_success_terminates() -> None:
    machine = _machine_at(CallState.TRANSFER_REQUESTED)
    machine.record_transfer_result(connected=True)
    assert machine.state is CallState.TRANSFERRED


def test_message_saved_moves_to_wrap_up() -> None:
    machine = _machine_at(CallState.TAKING_MESSAGE)
    machine.record_message_saved()
    assert machine.state is CallState.WRAPPING_UP


# --- mandatory overrides -----------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [s for s in ALL_STATES if s not in TERMINAL_STATES],
)
def test_emergency_overrides_any_active_state(state: CallState) -> None:
    machine = _machine_at(state)
    machine.data.emergency_detected = True
    result = machine.check_overrides()
    if state in (CallState.TRANSFER_REQUESTED, CallState.TRANSFER_IN_PROGRESS):
        assert result is None  # already escalating
    else:
        assert result is CallState.TRANSFER_REQUESTED


def test_human_request_overrides_routine_flow() -> None:
    machine = _machine_at(CallState.PRESENTING_SLOTS)
    machine.data.human_requested = True
    assert machine.check_overrides() is CallState.TRANSFER_REQUESTED


def test_max_duration_forces_message() -> None:
    machine = _machine_at(CallState.INTENT_DISCOVERY)
    machine.data.call_deadline = datetime.now(UTC) - timedelta(seconds=1)
    assert machine.check_overrides() is CallState.TAKING_MESSAGE


def test_overrides_do_not_fire_in_terminal_states() -> None:
    machine = _machine_at(CallState.COMPLETED)
    machine.data.emergency_detected = True
    assert machine.check_overrides() is None


def test_emergency_outranks_deadline() -> None:
    machine = _machine_at(CallState.INTENT_DISCOVERY)
    machine.data.emergency_detected = True
    machine.data.call_deadline = datetime.now(UTC) - timedelta(seconds=1)
    assert machine.check_overrides() is CallState.TRANSFER_REQUESTED


# --- LLM suggestions ---------------------------------------------------------


def test_llm_suggestion_applied_when_valid() -> None:
    machine = _machine_at(CallState.INTENT_DISCOVERY)
    assert machine.suggest_transition(CallState.SERVICE_IDENTIFICATION) is True
    assert machine.state is CallState.SERVICE_IDENTIFICATION


def test_llm_suggestion_rejected_when_invalid() -> None:
    machine = _machine_at(CallState.GREETING)
    assert machine.suggest_transition(CallState.BOOKED) is False
    assert machine.state is CallState.GREETING  # unchanged, no raise


def test_llm_cannot_skip_booking_gates() -> None:
    machine = _machine_at(CallState.CONFIRMING_BOOKING_DETAILS)
    assert machine.suggest_transition(CallState.BOOKING_IN_PROGRESS) is False


# --- failure counting and terminal failure -----------------------------------


def test_intent_failure_threshold_escalates() -> None:
    machine = _machine_at(CallState.INTENT_DISCOVERY)
    assert machine.record_intent_failure(threshold=2) is False
    assert machine.record_intent_failure(threshold=2) is True
    assert machine.state is CallState.TRANSFER_REQUESTED


def test_fail_is_terminal_from_anywhere() -> None:
    machine = _machine_at(CallState.PRESENTING_SLOTS)
    machine.fail("unhandled error")
    assert machine.state is CallState.FAILED
    with pytest.raises(InvalidTransitionError):
        machine.transition(CallState.WRAPPING_UP)


def test_snapshot_round_trips_history() -> None:
    machine = _machine()
    machine.transition(CallState.GREETING)
    machine.transition(CallState.INTENT_DISCOVERY)
    snapshot = machine.snapshot()
    assert snapshot["state"] == "intent_discovery"
    assert len(snapshot["history"]) == 2
    assert snapshot["data"]["tenant_id"] == "t1"
