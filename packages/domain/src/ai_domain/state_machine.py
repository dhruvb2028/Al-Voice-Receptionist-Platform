"""Deterministic conversation state machine.

The LLM may *suggest* a transition; only this module decides whether it
happens. Tool results and database records are authoritative over
anything the model says; mandatory overrides (emergency, human request,
maximum duration) outrank every routine flow.

The transition table is exhaustive: a transition absent from the table
is invalid, raises, and is counted — never silently applied.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger()


class CallState(StrEnum):
    CALL_STARTED = "call_started"
    GREETING = "greeting"
    INTENT_DISCOVERY = "intent_discovery"
    SERVICE_IDENTIFICATION = "service_identification"
    URGENCY_ASSESSMENT = "urgency_assessment"
    COLLECTING_NAME = "collecting_name"
    COLLECTING_PHONE = "collecting_phone"
    COLLECTING_ADDRESS = "collecting_address"
    COLLECTING_DETAILS = "collecting_details"
    CHECKING_SERVICE_AREA = "checking_service_area"
    CHECKING_AVAILABILITY = "checking_availability"
    PRESENTING_SLOTS = "presenting_slots"
    CONFIRMING_BOOKING_DETAILS = "confirming_booking_details"
    BOOKING_IN_PROGRESS = "booking_in_progress"
    BOOKED = "booked"
    TAKING_MESSAGE = "taking_message"
    TRANSFER_REQUESTED = "transfer_requested"
    TRANSFER_IN_PROGRESS = "transfer_in_progress"
    TRANSFERRED = "transferred"
    WRAPPING_UP = "wrapping_up"
    COMPLETED = "completed"
    FAILED = "failed"


#: states from which no further transition is permitted
TERMINAL_STATES = frozenset({CallState.TRANSFERRED, CallState.COMPLETED, CallState.FAILED})

#: mandatory-override targets reachable from any non-terminal state
_OVERRIDE_TARGETS = frozenset(
    {CallState.TRANSFER_REQUESTED, CallState.TAKING_MESSAGE, CallState.FAILED}
)

# state -> allowed next states (routine flow; overrides handled separately)
_TRANSITIONS: dict[CallState, frozenset[CallState]] = {
    CallState.CALL_STARTED: frozenset({CallState.GREETING}),
    CallState.GREETING: frozenset({CallState.INTENT_DISCOVERY}),
    CallState.INTENT_DISCOVERY: frozenset(
        {
            CallState.SERVICE_IDENTIFICATION,
            CallState.URGENCY_ASSESSMENT,
            CallState.TAKING_MESSAGE,
            CallState.WRAPPING_UP,
        }
    ),
    CallState.SERVICE_IDENTIFICATION: frozenset(
        {
            CallState.URGENCY_ASSESSMENT,
            CallState.COLLECTING_NAME,
            CallState.TAKING_MESSAGE,
            CallState.INTENT_DISCOVERY,
        }
    ),
    CallState.URGENCY_ASSESSMENT: frozenset(
        {
            CallState.COLLECTING_NAME,
            CallState.SERVICE_IDENTIFICATION,
            CallState.TAKING_MESSAGE,
        }
    ),
    CallState.COLLECTING_NAME: frozenset({CallState.COLLECTING_PHONE, CallState.TAKING_MESSAGE}),
    CallState.COLLECTING_PHONE: frozenset({CallState.COLLECTING_ADDRESS, CallState.TAKING_MESSAGE}),
    CallState.COLLECTING_ADDRESS: frozenset(
        {
            CallState.CHECKING_SERVICE_AREA,
            CallState.COLLECTING_DETAILS,
            CallState.TAKING_MESSAGE,
        }
    ),
    CallState.COLLECTING_DETAILS: frozenset(
        {CallState.CHECKING_SERVICE_AREA, CallState.CHECKING_AVAILABILITY, CallState.TAKING_MESSAGE}
    ),
    CallState.CHECKING_SERVICE_AREA: frozenset(
        {
            CallState.CHECKING_AVAILABILITY,
            CallState.COLLECTING_DETAILS,
            CallState.TAKING_MESSAGE,  # out of area -> message
        }
    ),
    CallState.CHECKING_AVAILABILITY: frozenset(
        {
            CallState.PRESENTING_SLOTS,
            CallState.TAKING_MESSAGE,  # no availability -> message
        }
    ),
    CallState.PRESENTING_SLOTS: frozenset(
        {
            CallState.CONFIRMING_BOOKING_DETAILS,
            CallState.CHECKING_AVAILABILITY,  # caller asks for other times
            CallState.TAKING_MESSAGE,
        }
    ),
    CallState.CONFIRMING_BOOKING_DETAILS: frozenset(
        {
            CallState.BOOKING_IN_PROGRESS,
            CallState.PRESENTING_SLOTS,  # caller changes slot
            CallState.COLLECTING_DETAILS,  # caller corrects a detail
            CallState.TAKING_MESSAGE,
        }
    ),
    CallState.BOOKING_IN_PROGRESS: frozenset(
        {
            CallState.BOOKED,  # only via record_booking_success
            CallState.PRESENTING_SLOTS,  # booking failed -> offer alternatives
            CallState.TAKING_MESSAGE,
        }
    ),
    CallState.BOOKED: frozenset({CallState.WRAPPING_UP}),
    CallState.TAKING_MESSAGE: frozenset({CallState.WRAPPING_UP, CallState.COMPLETED}),
    CallState.TRANSFER_REQUESTED: frozenset(
        {CallState.TRANSFER_IN_PROGRESS, CallState.TAKING_MESSAGE}
    ),
    CallState.TRANSFER_IN_PROGRESS: frozenset(
        {
            CallState.TRANSFERRED,
            CallState.TAKING_MESSAGE,  # transfer failed -> message fallback
        }
    ),
    CallState.TRANSFERRED: frozenset(),
    CallState.WRAPPING_UP: frozenset({CallState.COMPLETED, CallState.INTENT_DISCOVERY}),
    CallState.COMPLETED: frozenset(),
    CallState.FAILED: frozenset(),
}

# Booking prerequisites: fields that must be confirmed before
# BOOKING_IN_PROGRESS is reachable.
_BOOKING_REQUIRED_FIELDS = ("caller_name", "callback_number", "address", "service")


class InvalidTransitionError(Exception):
    def __init__(self, current: CallState, target: CallState, reason: str) -> None:
        super().__init__(f"{current.value} -> {target.value}: {reason}")
        self.current = current
        self.target = target
        self.reason = reason


class CallStateData(BaseModel):
    """Everything the state machine tracks for one call."""

    tenant_id: str
    call_id: str

    # caller identity + booking fields (unconfirmed until in confirmed_facts)
    caller_name: str | None = None
    callback_number: str | None = None
    address: str | None = None
    service: str | None = None
    problem_description: str | None = None
    urgency: str | None = None
    preferred_date: str | None = None

    service_area_ok: bool | None = None  # set only by the service-area tool
    presented_slots: list[str] = Field(default_factory=list)
    selected_slot: str | None = None
    booking_status: str | None = None  # pending | confirmed | failed — DB is authoritative
    message_status: str | None = None  # pending | saved | failed
    transfer_status: str | None = None  # dialing | connected | failed

    failed_intent_count: int = 0
    frustration_score: int = 0
    human_requested: bool = False
    emergency_detected: bool = False

    #: absolute wall-clock deadline from max_call_seconds
    call_deadline: datetime | None = None

    #: field name -> value the caller explicitly confirmed
    confirmed_facts: dict[str, str] = Field(default_factory=dict)

    def unresolved_fields(self) -> list[str]:
        """Booking-required fields not yet confirmed."""
        return [f for f in _BOOKING_REQUIRED_FIELDS if f not in self.confirmed_facts]

    def set_field(self, field: str, value: str) -> None:
        """Caller-provided value. Corrections replace earlier UNCONFIRMED
        values; a correction to a confirmed fact re-opens it."""
        setattr(self, field, value)
        # A new value invalidates any earlier confirmation of that field.
        self.confirmed_facts.pop(field, None)

    def confirm_field(self, field: str) -> None:
        value = getattr(self, field, None)
        if value is None:
            raise ValueError(f"cannot confirm unset field '{field}'")
        self.confirmed_facts[field] = str(value)


class ConversationStateMachine:
    """Validates and applies state transitions for one call."""

    def __init__(self, data: CallStateData, *, max_call_seconds: int = 900) -> None:
        self.data = data
        self.state = CallState.CALL_STARTED
        if data.call_deadline is None:
            from datetime import timedelta

            data.call_deadline = datetime.now(UTC) + timedelta(seconds=max_call_seconds)
        self.history: list[tuple[CallState, CallState, str]] = []

    # --- overrides ---------------------------------------------------------

    def check_overrides(self, *, now: datetime | None = None) -> CallState | None:
        """Mandatory overrides, in priority order. Returns the new state
        when one fires."""
        if self.state in TERMINAL_STATES:
            return None
        now = now or datetime.now(UTC)

        if self.data.emergency_detected and self.state not in (
            CallState.TRANSFER_REQUESTED,
            CallState.TRANSFER_IN_PROGRESS,
        ):
            return self._force(CallState.TRANSFER_REQUESTED, "emergency override")

        if self.data.human_requested and self.state not in (
            CallState.TRANSFER_REQUESTED,
            CallState.TRANSFER_IN_PROGRESS,
        ):
            return self._force(CallState.TRANSFER_REQUESTED, "human request override")

        if (
            self.data.call_deadline
            and now >= self.data.call_deadline
            and self.state is not CallState.TAKING_MESSAGE
        ):
            return self._force(CallState.TAKING_MESSAGE, "maximum duration override")
        return None

    def _force(self, target: CallState, reason: str) -> CallState:
        previous = self.state
        self.state = target
        self.history.append((previous, target, reason))
        logger.info(
            "state_override",
            from_state=previous.value,
            to_state=target.value,
            reason=reason,
        )
        return target

    # --- routine transitions ----------------------------------------------

    def can_transition(self, target: CallState) -> tuple[bool, str]:
        if self.state in TERMINAL_STATES:
            return False, "call is in a terminal state"
        if target in _OVERRIDE_TARGETS and target is not CallState.FAILED:
            # transfer/message are reachable from any non-terminal state
            return True, "override target"
        allowed = _TRANSITIONS.get(self.state, frozenset())
        if target not in allowed:
            return False, "not in transition table"
        if target is CallState.BOOKING_IN_PROGRESS:
            missing = self.data.unresolved_fields()
            if missing:
                return False, f"booking blocked; unconfirmed fields: {', '.join(missing)}"
            if self.data.service_area_ok is not True:
                return False, "booking blocked; service area not verified"
            if self.data.selected_slot is None:
                return False, "booking blocked; no slot selected"
        if target is CallState.BOOKED:
            # Only record_booking_success may enter BOOKED.
            return False, "BOOKED is entered only by record_booking_success"
        return True, "ok"

    def transition(self, target: CallState, *, suggested_by: str = "domain") -> CallState:
        """Apply a validated transition. ``suggested_by='llm'`` marks
        model-proposed moves; validation is identical — suggestions get
        no extra authority."""
        ok, reason = self.can_transition(target)
        if not ok:
            raise InvalidTransitionError(self.state, target, reason)
        previous = self.state
        self.state = target
        self.history.append((previous, target, f"{suggested_by}: {reason}"))
        return target

    def suggest_transition(self, target: CallState) -> bool:
        """LLM-suggested transition: applied only when valid; an invalid
        suggestion is rejected (and logged), never raises mid-call."""
        ok, reason = self.can_transition(target)
        if not ok:
            logger.info(
                "llm_transition_rejected",
                from_state=self.state.value,
                to_state=target.value,
                reason=reason,
            )
            return False
        self.transition(target, suggested_by="llm")
        return True

    # --- authoritative results --------------------------------------------

    def record_service_area_result(self, in_area: bool) -> None:
        """Tool result — authoritative regardless of what the model said."""
        self.data.service_area_ok = in_area
        if self.state is CallState.CHECKING_SERVICE_AREA:
            self.transition(
                CallState.CHECKING_AVAILABILITY if in_area else CallState.TAKING_MESSAGE
            )

    def record_slots_presented(self, slots: list[str]) -> None:
        self.data.presented_slots = slots
        if self.state is CallState.CHECKING_AVAILABILITY:
            self.transition(CallState.PRESENTING_SLOTS if slots else CallState.TAKING_MESSAGE)

    def record_booking_success(self, *, booking_id: str) -> None:
        """Database commit is the only path into BOOKED — spoken
        confirmation gates on this."""
        if self.state is not CallState.BOOKING_IN_PROGRESS:
            raise InvalidTransitionError(
                self.state, CallState.BOOKED, "booking success outside booking_in_progress"
            )
        self.data.booking_status = "confirmed"
        previous = self.state
        self.state = CallState.BOOKED
        self.history.append((previous, CallState.BOOKED, f"booking committed: {booking_id}"))

    def record_booking_failure(self) -> None:
        if self.state is not CallState.BOOKING_IN_PROGRESS:
            return
        self.data.booking_status = "failed"
        self.transition(CallState.PRESENTING_SLOTS)

    def record_transfer_result(self, *, connected: bool) -> None:
        self.data.transfer_status = "connected" if connected else "failed"
        if self.state is CallState.TRANSFER_REQUESTED:
            self.transition(CallState.TRANSFER_IN_PROGRESS)
        if self.state is CallState.TRANSFER_IN_PROGRESS:
            if connected:
                self.transition(CallState.TRANSFERRED)
            else:
                self.transition(CallState.TAKING_MESSAGE)

    def record_message_saved(self) -> None:
        self.data.message_status = "saved"
        if self.state is CallState.TAKING_MESSAGE:
            self.transition(CallState.WRAPPING_UP)

    def record_intent_failure(self, *, threshold: int) -> bool:
        """Returns True when the failure threshold forces escalation."""
        self.data.failed_intent_count += 1
        if self.data.failed_intent_count >= threshold and self.state not in TERMINAL_STATES:
            self._force(CallState.TRANSFER_REQUESTED, "failed-intent threshold")
            return True
        return False

    def fail(self, reason: str) -> None:
        """Unrecoverable error — terminal FAILED from any state."""
        previous = self.state
        self.state = CallState.FAILED
        self.history.append((previous, CallState.FAILED, f"failed: {reason}"))

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "data": self.data.model_dump(mode="json"),
            "history": [{"from": f.value, "to": t.value, "reason": r} for f, t, r in self.history],
        }
