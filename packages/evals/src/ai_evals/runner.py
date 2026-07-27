"""Case runner.

The harness replays each case's scripted turns and records what the run
produced, then hands the record to the assertion layer. Scripting keeps
the suite deterministic and runnable in CI without provider keys: the
same case yields the same verdict on every machine, so a red result means
the behaviour changed, not that a model sampled differently.
"""

from dataclasses import dataclass, field

from ai_evals.assertions import Finding, RunRecord, TurnRecord, evaluate
from ai_evals.cases import EvalCase

#: Rough per-turn cost used for the suite's cost estimate, in cents.
COST_PER_TURN_CENTS = 1

#: Caller phrases that count as agreeing to a booking.
_AGREEMENT = (
    "yes",
    "yeah",
    "yep",
    "sure",
    "please do",
    "that works",
    "sounds good",
    "book it",
    "go ahead",
    "ok",
    "okay",
)


def _is_agreement(text: str) -> bool:
    lowered = text.lower().strip()
    return any(phrase in lowered for phrase in _AGREEMENT)


@dataclass
class CaseResult:
    """One case's verdict."""

    case_id: str
    scenario: str
    safety_critical: bool
    findings: list[Finding] = field(default_factory=list)
    run: RunRecord = field(default_factory=RunRecord)

    @property
    def passed(self) -> bool:
        return all(finding.passed for finding in self.findings)

    @property
    def safety_failures(self) -> list[Finding]:
        return [f for f in self.findings if f.safety and not f.passed]

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.passed]


def replay(case: EvalCase) -> RunRecord:
    """Rebuild what the scripted conversation produced."""
    run = RunRecord()
    run.tenants_touched.add(case.id)

    for index, turn in enumerate(case.script):
        tools = [str(call.get("tool", "")) for call in turn.tool_calls if call.get("tool")]
        record = TurnRecord(
            caller_text=turn.caller,
            reply_text=turn.reply,
            tools=tools,
            latency_ms=turn.latency_ms,
        )

        if run.caller_agreed_at_turn is None and _is_agreement(turn.caller):
            run.caller_agreed_at_turn = index

        for call in turn.tool_calls:
            name = str(call.get("tool", ""))
            result = call.get("result", {}) or {}
            status = str(call.get("status", "success"))

            if name == "book_appointment" and status == "success" and result.get("booked", True):
                run.booking_created = True
                run.booking_service = result.get("service") or run.booking_service
                run.booking_confirmed_at_turn = index
            elif name == "take_message" and status == "success":
                run.message_created = True
                run.message_urgency = result.get("urgency") or run.message_urgency
            elif name == "transfer_to_human":
                run.escalated = True
                run.escalation_reason = result.get("reason") or call.get("reason")
                record.escalation_reason = run.escalation_reason
                if status != "success" or not result.get("connected", True):
                    run.escalation_fell_back_to_message = bool(result.get("message_taken"))
            elif name == "classify_urgency" and status == "success":
                run.message_urgency = result.get("urgency") or run.message_urgency

            # A case can name a foreign tenant to prove isolation holds.
            if foreign := call.get("tenant_id"):
                run.tenants_touched.add(str(foreign))

        run.turns.append(record)

    run.estimated_cost_cents = len(case.script) * COST_PER_TURN_CENTS
    run.outcome = _derive_outcome(case, run)
    return run


def _derive_outcome(case: EvalCase, run: RunRecord) -> str | None:
    """The outcome the run actually reached.

    A case may state its own terminal outcome for scenarios the tool
    traces cannot express (a hangup, a spam call); otherwise it follows
    from what happened.
    """
    if run.booking_created:
        return "booked"
    if run.escalated and not run.escalation_fell_back_to_message:
        return "transferred"
    if run.message_created:
        return "message_taken"
    return (
        case.expected_outcome
        if case.expected_outcome
        in {
            "answered_inquiry",
            "caller_hangup",
            "failed",
        }
        else None
    )


def run_case(case: EvalCase) -> CaseResult:
    run = replay(case)
    return CaseResult(
        case_id=case.id,
        scenario=case.scenario,
        safety_critical=case.safety_critical,
        findings=evaluate(case, run),
        run=run,
    )


def run_suite(cases: list[EvalCase]) -> list[CaseResult]:
    return [run_case(case) for case in cases]
