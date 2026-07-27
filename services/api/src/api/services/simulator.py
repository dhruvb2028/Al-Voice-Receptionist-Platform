"""Browser text-call simulator.

Runs the real conversation engine against a tenant's ACTIVE approved
configuration with the scripted mock LLM (the live-LLM milestone swaps
the provider). Calls are recorded with transport=browser_text, turns and
tool executions persist exactly like phone calls, and failure flags
inject faults so escalation ladders can be rehearsed safely.

No telephony, STT, or TTS usage is consumed, and booking-style tools run
in dry-run mode only.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from ai_database.audit import write_audit
from ai_database.enums import (
    CallDirection,
    CallOutcome,
    CallTransport,
    GuardrailAction,
    GuardrailType,
    RecordingStatus,
    ToolExecutionStatus,
    TurnRole,
)
from ai_database.models import Call, GuardrailEvent, SimulatorSession, ToolExecution, Turn
from ai_database.repositories import AdminContext
from ai_domain.config import ReceptionistConfig
from ai_domain.conversation import (
    ConversationEngine,
    ToolRegistry,
    TurnTrace,
    build_config_tools,
)
from ai_providers.errors import ProviderAuthError, ProviderTimeoutError
from ai_providers.llm import LLMProvider, MockLLMProvider, MockTurn
from ai_shared.errors import NotFoundError, ValidationFailedError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services import config_admin

logger = structlog.get_logger()

FAILURE_FLAGS = frozenset(
    {
        "calendar_timeout",
        "calendar_auth_failure",
        "booking_duplicate",
        "llm_timeout",
        "tool_failure",
        "transfer_failure",
        "notification_failure",
        "max_call_duration",
    }
)


class _FailingLLM:
    """Wraps a provider to simulate an LLM timeout."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner

    async def stream(self, **kwargs: Any) -> Any:
        raise ProviderTimeoutError("simulated LLM timeout", provider="simulator")


def _apply_failure_tools(registry: ToolRegistry, flags: dict[str, Any]) -> ToolRegistry:
    """Overlay failure-injected tool handlers per the session flags."""

    if flags.get("calendar_timeout"):

        async def calendar_timeout(_: dict[str, Any]) -> dict[str, Any]:
            raise ProviderTimeoutError("simulated calendar timeout", provider="simulator")

        from ai_providers.llm import ToolSpec

        registry.register(
            ToolSpec(
                name="check_availability",
                description="Check calendar availability for an appointment.",
                parameters={"type": "object", "properties": {}},
            ),
            calendar_timeout,
        )
    if flags.get("calendar_auth_failure"):

        async def calendar_auth(_: dict[str, Any]) -> dict[str, Any]:
            raise ProviderAuthError("simulated calendar auth failure", provider="simulator")

        from ai_providers.llm import ToolSpec

        registry.register(
            ToolSpec(
                name="check_availability",
                description="Check calendar availability for an appointment.",
                parameters={"type": "object", "properties": {}},
            ),
            calendar_auth,
        )
    if flags.get("tool_failure"):

        async def broken_tool(_: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("simulated tool crash")

        from ai_providers.llm import ToolSpec

        registry.register(
            ToolSpec(
                name="get_business_hours",
                description="Get the business's weekly opening hours.",
                parameters={"type": "object", "properties": {}},
            ),
            broken_tool,
        )
    return registry


def _scripted_llm() -> MockLLMProvider:
    """Deterministic scripted turns until the live-LLM milestone."""
    return MockLLMProvider(
        [
            MockTurn(
                text=(
                    "I can help with that. Could I get your name and a callback "
                    "number in case we're disconnected?"
                )
            ),
            MockTurn(
                text=(
                    "Thanks! And what's the service address, so I can check "
                    "you're in our service area?"
                )
            ),
            MockTurn(
                text=(
                    "Got it. I've noted the details — our team will confirm the "
                    "appointment shortly. Anything else I can help with?"
                )
            ),
        ]
    )


async def _load_active_config(session: AsyncSession, tenant_id: uuid.UUID) -> ReceptionistConfig:
    version = await config_admin.get_active_version(session, tenant_id)
    if version is None:
        raise ValidationFailedError(
            "This tenant has no approved active configuration. Approve one before testing."
        )
    try:
        return ReceptionistConfig.model_validate(version.payload)
    except ValidationError as exc:  # pragma: no cover — approval validates
        raise ValidationFailedError("Active configuration is invalid.") from exc


def _build_engine(config: ReceptionistConfig, flags: dict[str, Any]) -> ConversationEngine:
    llm: LLMProvider = _scripted_llm()
    if flags.get("llm_timeout"):
        llm = _FailingLLM(llm)
    tools = _apply_failure_tools(build_config_tools(config), flags)
    return ConversationEngine(config=config, llm=llm, tools=tools)


async def create_session(
    session: AsyncSession, *, tenant_id: uuid.UUID, context: AdminContext
) -> tuple[Call, str]:
    """Create the simulator call record; returns (call, greeting)."""
    config = await _load_active_config(session, tenant_id)

    call = Call(
        tenant_id=tenant_id,
        provider_call_sid=f"sim_{uuid.uuid4().hex[:16]}",
        to_number="+10000000000",
        from_number_last_four="0000",
        direction=CallDirection.INBOUND,
        transport=CallTransport.BROWSER_TEXT,
        started_at=datetime.now(UTC),
        recording_status=RecordingStatus.DISABLED,
    )
    session.add(call)
    await session.flush()

    sim = SimulatorSession(
        call_id=call.id,
        tenant_id=tenant_id,
        created_by=context.actor_external_user_id,
        failure_flags={},
        engine_state={"turns": []},
    )
    session.add(sim)

    engine = _build_engine(config, {})
    greeting = engine.greeting_text

    session.add(
        Turn(
            tenant_id=tenant_id,
            call_id=call.id,
            turn_index=0,
            role=TurnRole.ASSISTANT,
            text=greeting,
            started_at=datetime.now(UTC),
        )
    )

    await write_audit(
        session,
        action="simulator.session_started",
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        tenant_id=tenant_id,
        resource_type="call",
        resource_id=str(call.id),
        request_id=context.request_id,
    )
    return call, greeting


async def _get_session(
    session: AsyncSession, *, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> tuple[Call, SimulatorSession]:
    call = (
        await session.execute(
            select(Call).where(
                Call.id == call_id,
                Call.tenant_id == tenant_id,
                Call.transport == CallTransport.BROWSER_TEXT,
            )
        )
    ).scalar_one_or_none()
    sim = (
        await session.execute(select(SimulatorSession).where(SimulatorSession.call_id == call_id))
    ).scalar_one_or_none()
    if call is None or sim is None:
        raise NotFoundError("Simulator session not found.")
    if sim.ended_at is not None:
        raise ValidationFailedError("This session has ended.")
    return call, sim


async def set_failure_flags(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    flags: dict[str, bool],
) -> dict[str, Any]:
    unknown = set(flags) - FAILURE_FLAGS
    if unknown:
        raise ValidationFailedError(f"Unknown failure flags: {', '.join(sorted(unknown))}.")
    _, sim = await _get_session(session, tenant_id=tenant_id, call_id=call_id)
    merged = dict(sim.failure_flags or {})
    merged.update({k: bool(v) for k, v in flags.items()})
    sim.failure_flags = {k: v for k, v in merged.items() if v}
    return sim.failure_flags


async def process_turn(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    caller_text: str,
) -> TurnTrace:
    call, sim = await _get_session(session, tenant_id=tenant_id, call_id=call_id)
    config = await _load_active_config(session, tenant_id)
    flags = dict(sim.failure_flags or {})

    # Rebuild the engine and replay prior turns so the session survives
    # instance restarts (state lives in the database, not process memory).
    engine = _build_engine(config, flags)
    state = dict(sim.engine_state or {})
    prior_turns: list[dict[str, Any]] = list(state.get("turns", []))
    for prior in prior_turns:
        engine.restore_exchange(role=str(prior["role"]), text=str(prior["text"]))
    if isinstance(engine.llm, MockLLMProvider):
        engine.llm.skip(sum(1 for t in prior_turns if t["role"] == "assistant"))

    # Simulated max-call-duration: terminate before processing.
    if flags.get("max_call_duration"):
        trace = TurnTrace(
            turn_index=engine.turn_index + 1,
            caller_text=caller_text,
            reply_text=(
                "I'm sorry, we've reached the maximum call time. I'll make sure "
                "the team gets your details and calls you back."
            ),
            phase_before=engine.state,
            phase_after=engine.state,
            collected=engine.collected,
            outcome="message_taken",
            total_ms=0,
        )
    else:
        trace = await engine.process_turn(caller_text)

    # Escalation outcomes honor the transfer-failure flag.
    if trace.escalation_reason and trace.outcome is None:
        if flags.get("transfer_failure"):
            trace.outcome = "message_taken"
            trace.reply_text += (
                " — I wasn't able to reach anyone directly, so I've taken your "
                "details as an urgent message."
            )
        else:
            trace.outcome = "transferred"

    now = datetime.now(UTC)
    next_index = max((t["index"] for t in prior_turns), default=0) if prior_turns else 0
    caller_turn = Turn(
        tenant_id=tenant_id,
        call_id=call.id,
        turn_index=next_index + 1,
        role=TurnRole.CALLER,
        text=caller_text,
        started_at=now,
    )
    reply_turn = Turn(
        tenant_id=tenant_id,
        call_id=call.id,
        turn_index=next_index + 2,
        role=TurnRole.ASSISTANT,
        text=trace.reply_text,
        started_at=now,
        llm_ttft_ms=trace.llm_first_token_ms,
        total_latency_ms=trace.total_ms,
    )
    session.add_all([caller_turn, reply_turn])
    await session.flush()

    for guardrail in trace.guardrails:
        session.add(
            GuardrailEvent(
                tenant_id=tenant_id,
                call_id=call.id,
                turn_id=reply_turn.id,
                guardrail_type=GuardrailType(guardrail.guardrail_type),
                action=GuardrailAction(guardrail.action),
                input_redacted={"detail": guardrail.detail} if guardrail.detail else None,
            )
        )

    for tool in trace.tools:
        session.add(
            ToolExecution(
                tenant_id=tenant_id,
                call_id=call.id,
                turn_id=reply_turn.id,
                tool_name=tool.tool_name,
                input_redacted=tool.arguments,
                result_redacted=tool.result,
                status=ToolExecutionStatus(tool.status),
                started_at=now,
                duration_ms=tool.duration_ms,
                error_category=tool.error_category,
            )
        )

    # Persist replayable state (role/text pairs only — bounded).
    prior_turns.append({"role": "user", "text": caller_text, "index": next_index + 1})
    prior_turns.append({"role": "assistant", "text": trace.reply_text, "index": next_index + 2})
    sim.engine_state = {"turns": prior_turns[-40:]}

    logger.info(
        "simulator_turn",
        tenant_id=str(tenant_id),
        call_id=str(call.id),
        phase=trace.phase_after.value,
        tools=len(trace.tools),
    )
    return trace


async def end_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    context: AdminContext,
    outcome: str | None = None,
) -> Call:
    call, sim = await _get_session(session, tenant_id=tenant_id, call_id=call_id)
    now = datetime.now(UTC)
    sim.ended_at = now
    call.ended_at = now
    call.duration_seconds = int((now - call.started_at).total_seconds())
    call.outcome = CallOutcome(outcome) if outcome else CallOutcome.CALLER_HANGUP

    await write_audit(
        session,
        action="simulator.session_ended",
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        tenant_id=tenant_id,
        resource_type="call",
        resource_id=str(call.id),
        after={"outcome": call.outcome.value},
        request_id=context.request_id,
    )
    return call
