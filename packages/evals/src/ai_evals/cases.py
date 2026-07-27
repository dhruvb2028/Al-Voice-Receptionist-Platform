"""Evaluation case format.

Cases are version-controlled YAML so a behaviour change shows up as a
reviewable diff. The schema is strict — an unknown key is a typo, and a
typo in an expectation silently weakens the suite, so loading rejects it
rather than ignoring it.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

#: Every scenario the suite is required to cover. The loader checks the
#: catalog against this list so a deleted case is a visible failure.
REQUIRED_SCENARIOS: tuple[str, ...] = (
    "normal_booking",
    "unavailable_slot",
    "changed_date",
    "changed_service",
    "outside_business_hours",
    "outside_service_area",
    "unsupported_service",
    "price_question",
    "discount_manipulation",
    "emergency_leak",
    "gas_smell",
    "electrical_danger",
    "angry_caller",
    "human_request",
    "wrong_number",
    "spam",
    "silence",
    "rambling_caller",
    "background_noise",
    "missing_name",
    "missing_address",
    "calendar_timeout",
    "calendar_revoked",
    "duplicate_booking",
    "sms_failure",
    "transfer_failure",
    "llm_timeout",
    "tts_timeout",
    "caller_interruption",
    "max_call_duration",
    "prompt_injection",
    "cross_tenant_request",
)


class CasePersona(BaseModel):
    """Who is calling and how they behave."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    mood: Literal["neutral", "hurried", "angry", "confused", "hostile"] = "neutral"
    description: str = ""


class ScriptTurn(BaseModel):
    """One exchange: what the caller says, and what the model replies.

    ``reply`` is scripted so the harness is deterministic and runs in CI
    without provider keys. ``tool_calls`` lets a case drive the tool path
    the model would have taken.
    """

    model_config = ConfigDict(extra="forbid")

    caller: str
    reply: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    #: simulated end-to-end latency for this turn, in milliseconds
    latency_ms: int | None = None


class ExpectedBooking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: bool = False
    service: str | None = None
    #: a booking must never be confirmed before the caller agreed
    requires_confirmation: bool = True


class ExpectedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: bool = False
    urgency: Literal["emergency", "urgent", "routine"] | None = None


class ExpectedEscalation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred: bool = False
    reason: Literal["emergency", "human_request", "intent_failure", "system_error"] | None = None
    #: when a transfer fails, a message must be taken instead
    falls_back_to_message: bool = False


class LatencyThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p50_ms: int = 1500
    p95_ms: int = 2500


class CaseTenant(BaseModel):
    """Tenant configuration overrides for this case."""

    model_config = ConfigDict(extra="forbid")

    business_name: str = "Ace Plumbing"
    timezone: str = "America/New_York"
    services: list[str] = Field(default_factory=lambda: ["Drain Cleaning", "Leak Repair"])
    #: service name -> approved customer-visible price label
    approved_prices: dict[str, str] = Field(default_factory=dict)
    postal_codes: list[str] = Field(default_factory=lambda: ["11201"])
    open_hours: str = "09:00-17:00"
    max_call_seconds: int = 900


class EvalCase(BaseModel):
    """One evaluation scenario."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=80)
    scenario: str = Field(min_length=3, max_length=80)
    description: str = ""
    tenant: CaseTenant = Field(default_factory=CaseTenant)
    persona: CasePersona = Field(default_factory=CasePersona)
    script: list[ScriptTurn] = Field(min_length=1)

    expected_intent: str | None = None
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    #: tools that must run in this relative order when all are present
    expected_tool_order: list[str] = Field(default_factory=list)
    expected_outcome: (
        Literal[
            "booked",
            "message_taken",
            "transferred",
            "answered_inquiry",
            "caller_hangup",
            "failed",
        ]
        | None
    ) = None
    expected_booking: ExpectedBooking = Field(default_factory=ExpectedBooking)
    expected_message: ExpectedMessage = Field(default_factory=ExpectedMessage)
    expected_escalation: ExpectedEscalation = Field(default_factory=ExpectedEscalation)
    #: substrings the receptionist must never say (invented prices,
    #: guarantees, or claims the tenant never approved)
    forbidden_claims: list[str] = Field(default_factory=list)
    latency: LatencyThresholds = Field(default_factory=LatencyThresholds)
    #: a case may assert it must not leak another tenant's data
    tenant_isolation: bool = False
    #: safety cases fail CI on regression regardless of overall pass rate
    safety_critical: bool = False


def load_case(path: Path) -> EvalCase:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: case file must be a mapping")
    return EvalCase.model_validate(raw)


def load_cases(directory: Path) -> list[EvalCase]:
    """Every case in a directory, sorted by id for stable reports."""
    cases = [load_case(path) for path in sorted(directory.glob("*.yaml"))]
    ids = [case.id for case in cases]
    duplicates = {name for name in ids if ids.count(name) > 1}
    if duplicates:
        raise ValueError(f"duplicate case ids: {sorted(duplicates)}")
    return sorted(cases, key=lambda case: case.id)


def missing_scenarios(cases: list[EvalCase]) -> list[str]:
    """Required scenarios with no case covering them."""
    covered = {case.scenario for case in cases}
    return [scenario for scenario in REQUIRED_SCENARIOS if scenario not in covered]
