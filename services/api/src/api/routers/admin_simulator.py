"""Admin browser-simulator endpoints.

Platform administrators only — unrestricted tenant testing lives here.
(A limited client-owner testing mode may arrive later; it will be a
separate, restricted surface.)
"""

import uuid
from typing import Annotated, Any

from ai_database.repositories import AdminContext
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import require_platform_admin
from api.db import get_session
from api.services import simulator

router = APIRouter(prefix="/admin/tenants/{tenant_id}/simulator", tags=["admin-simulator"])


class SessionCreatedResponse(BaseModel):
    call_id: uuid.UUID
    greeting: str


class TurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ToolTraceView(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    status: str
    duration_ms: int
    error_category: str | None


class GuardrailView(BaseModel):
    guardrail_type: str
    action: str
    detail: str | None


class TurnResponse(BaseModel):
    turn_index: int
    reply_text: str
    phase_before: str
    phase_after: str
    collected: dict[str, Any]
    tools: list[ToolTraceView]
    guardrails: list[GuardrailView]
    llm_first_token_ms: int | None
    llm_total_ms: int | None
    total_ms: int | None
    failed_intent_count: int
    escalation_reason: str | None
    outcome: str | None


class FailureFlagsRequest(BaseModel):
    flags: dict[str, bool]


class FailureFlagsResponse(BaseModel):
    flags: dict[str, bool]
    available: list[str]


class EndRequest(BaseModel):
    outcome: str | None = Field(default=None, pattern="^[a-z_]+$")


class EndResponse(BaseModel):
    call_id: uuid.UUID
    outcome: str
    duration_seconds: int | None


@router.post("/sessions", status_code=201)
async def create_session(
    tenant_id: uuid.UUID,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SessionCreatedResponse:
    call, greeting = await simulator.create_session(session, tenant_id=tenant_id, context=context)
    return SessionCreatedResponse(call_id=call.id, greeting=greeting)


@router.post("/sessions/{call_id}/turns")
async def process_turn(
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    request: TurnRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TurnResponse:
    trace = await simulator.process_turn(
        session, tenant_id=tenant_id, call_id=call_id, caller_text=request.text
    )
    return TurnResponse(
        turn_index=trace.turn_index,
        reply_text=trace.reply_text,
        phase_before=trace.phase_before.value,
        phase_after=trace.phase_after.value,
        collected=trace.collected.model_dump(),
        tools=[ToolTraceView(**tool.model_dump()) for tool in trace.tools],
        guardrails=[GuardrailView(**g.model_dump()) for g in trace.guardrails],
        llm_first_token_ms=trace.llm_first_token_ms,
        llm_total_ms=trace.llm_total_ms,
        total_ms=trace.total_ms,
        failed_intent_count=trace.failed_intent_count,
        escalation_reason=trace.escalation_reason,
        outcome=trace.outcome,
    )


@router.put("/sessions/{call_id}/failures")
async def set_failures(
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    request: FailureFlagsRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FailureFlagsResponse:
    flags = await simulator.set_failure_flags(
        session, tenant_id=tenant_id, call_id=call_id, flags=request.flags
    )
    return FailureFlagsResponse(
        flags={k: bool(v) for k, v in flags.items()},
        available=sorted(simulator.FAILURE_FLAGS),
    )


@router.post("/sessions/{call_id}/end")
async def end_session(
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    request: EndRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EndResponse:
    call = await simulator.end_session(
        session,
        tenant_id=tenant_id,
        call_id=call_id,
        context=context,
        outcome=request.outcome,
    )
    return EndResponse(
        call_id=call.id,
        outcome=call.outcome.value if call.outcome else "caller_hangup",
        duration_seconds=call.duration_seconds,
    )
