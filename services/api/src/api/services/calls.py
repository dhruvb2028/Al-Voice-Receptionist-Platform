"""Call history queries for the client dashboard and admin console.

Everything here is tenant-scoped by construction: every query filters on
the tenant id taken from the verified principal (or the admin-selected
tenant), and cross-tenant call ids resolve to None. The client payload
carries no provider identifiers or internal latency data — those fields
appear only when ``admin=True``.
"""

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ai_database.enums import BookingStatus, CallOutcome, RecordingStatus, Urgency
from ai_database.models import (
    AuditLog,
    Booking,
    Call,
    Escalation,
    GuardrailEvent,
    Message,
    Service,
    ToolExecution,
    Turn,
    UsageRecord,
)
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_PAGE_SIZE = 100
EXPORT_ROW_CAP = 1000

_SORTS: dict[str, ColumnElement[Any]] = {
    "started_at": Call.started_at.asc(),
    "-started_at": Call.started_at.desc(),
    "duration": Call.duration_seconds.asc().nulls_last(),
    "-duration": Call.duration_seconds.desc().nulls_last(),
}


@dataclass(frozen=True)
class CallListFilters:
    search: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    outcome: CallOutcome | None = None
    urgency: Urgency | None = None
    booking: str | None = None  # confirmed | pending | none
    sort: str = "-started_at"
    page: int = 1
    page_size: int = 25


class CallListItem(BaseModel):
    id: uuid.UUID
    started_at: datetime
    from_number_last_four: str | None
    duration_seconds: int | None
    outcome: str | None
    service: str | None
    urgency: str | None
    booking_status: str | None
    transfer_status: str | None
    recording_available: bool
    processing_status: str
    transport: str


class CallListPage(BaseModel):
    items: list[CallListItem]
    total: int
    page: int
    page_size: int


def _apply_filters(stmt: Select[Any], tenant_id: uuid.UUID, f: CallListFilters) -> Select[Any]:
    stmt = stmt.where(Call.tenant_id == tenant_id)
    if f.search:
        term = f.search.strip()
        if term.isdigit() and len(term) == 4:
            stmt = stmt.where(Call.from_number_last_four == term)
        else:
            pattern = f"%{term}%"
            stmt = stmt.where(
                or_(
                    select(Booking.id)
                    .where(Booking.call_id == Call.id, Booking.customer_name.ilike(pattern))
                    .exists(),
                    select(Message.id)
                    .where(Message.call_id == Call.id, Message.customer_name.ilike(pattern))
                    .exists(),
                )
            )
    if f.date_from:
        stmt = stmt.where(Call.started_at >= f.date_from)
    if f.date_to:
        stmt = stmt.where(Call.started_at <= f.date_to)
    if f.outcome:
        stmt = stmt.where(Call.outcome == f.outcome)
    if f.urgency:
        stmt = stmt.where(Call.urgency == f.urgency)
    if f.booking == "confirmed":
        stmt = stmt.where(
            select(Booking.id)
            .where(Booking.call_id == Call.id, Booking.status == BookingStatus.CONFIRMED)
            .exists()
        )
    elif f.booking == "pending":
        stmt = stmt.where(
            select(Booking.id)
            .where(Booking.call_id == Call.id, Booking.status != BookingStatus.CONFIRMED)
            .exists()
        )
    elif f.booking == "none":
        stmt = stmt.where(~select(Booking.id).where(Booking.call_id == Call.id).exists())
    return stmt


async def _related_by_call(
    session: AsyncSession, tenant_id: uuid.UUID, call_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, Booking], dict[uuid.UUID, Escalation], dict[uuid.UUID, str]]:
    """Bookings, escalations, and service names for a page of calls."""
    if not call_ids:
        return {}, {}, {}
    bookings: dict[uuid.UUID, Booking] = {}
    for booking in (
        (
            await session.execute(
                select(Booking).where(Booking.tenant_id == tenant_id, Booking.call_id.in_(call_ids))
            )
        )
        .scalars()
        .all()
    ):
        assert booking.call_id is not None
        # A confirmed booking wins over earlier failed attempts.
        current = bookings.get(booking.call_id)
        if current is None or booking.status is BookingStatus.CONFIRMED:
            bookings[booking.call_id] = booking

    escalations: dict[uuid.UUID, Escalation] = {}
    for escalation in (
        (
            await session.execute(
                select(Escalation).where(
                    Escalation.tenant_id == tenant_id, Escalation.call_id.in_(call_ids)
                )
            )
        )
        .scalars()
        .all()
    ):
        escalations[escalation.call_id] = escalation

    service_ids = {b.service_id for b in bookings.values() if b.service_id is not None}
    service_names: dict[uuid.UUID, str] = {}
    if service_ids:
        for service in (
            (await session.execute(select(Service).where(Service.id.in_(service_ids))))
            .scalars()
            .all()
        ):
            service_names[service.id] = service.name

    return bookings, escalations, service_names


def _list_item(
    call: Call,
    booking: Booking | None,
    escalation: Escalation | None,
    service_name: str | None,
) -> CallListItem:
    return CallListItem(
        id=call.id,
        started_at=call.started_at,
        from_number_last_four=call.from_number_last_four,
        duration_seconds=call.duration_seconds,
        outcome=call.outcome.value if call.outcome else None,
        service=service_name,
        urgency=call.urgency.value if call.urgency else None,
        booking_status=booking.status.value if booking else None,
        transfer_status=escalation.status.value if escalation else None,
        recording_available=call.recording_status is RecordingStatus.STORED,
        processing_status=call.post_processing_status.value,
        transport=call.transport.value,
    )


async def _load_page(
    session: AsyncSession, tenant_id: uuid.UUID, f: CallListFilters, *, limit: int, offset: int
) -> list[CallListItem]:
    order = _SORTS.get(f.sort, _SORTS["-started_at"])
    stmt = _apply_filters(select(Call), tenant_id, f).order_by(order, Call.id).limit(limit)
    if offset:
        stmt = stmt.offset(offset)
    calls = (await session.execute(stmt)).scalars().all()
    bookings, escalations, service_names = await _related_by_call(
        session, tenant_id, [c.id for c in calls]
    )
    items = []
    for call in calls:
        booking = bookings.get(call.id)
        items.append(
            _list_item(
                call,
                booking,
                escalations.get(call.id),
                service_names.get(booking.service_id) if booking and booking.service_id else None,
            )
        )
    return items


async def list_calls(
    session: AsyncSession, tenant_id: uuid.UUID, f: CallListFilters
) -> CallListPage:
    page = max(1, f.page)
    page_size = min(max(1, f.page_size), MAX_PAGE_SIZE)
    total = (
        await session.execute(_apply_filters(select(func.count(Call.id)), tenant_id, f))
    ).scalar_one()
    items = await _load_page(session, tenant_id, f, limit=page_size, offset=(page - 1) * page_size)
    return CallListPage(items=items, total=total, page=page, page_size=page_size)


async def export_calls_csv(session: AsyncSession, tenant_id: uuid.UUID, f: CallListFilters) -> str:
    """CSV of the filtered call history (capped, newest first)."""
    items = await _load_page(session, tenant_id, f, limit=EXPORT_ROW_CAP, offset=0)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "started_at",
            "caller_last_four",
            "duration_seconds",
            "outcome",
            "service",
            "urgency",
            "booking_status",
            "transfer_status",
            "recording_available",
            "processing_status",
        ]
    )
    for item in items:
        writer.writerow(
            [
                item.started_at.isoformat(),
                item.from_number_last_four or "",
                item.duration_seconds if item.duration_seconds is not None else "",
                item.outcome or "",
                item.service or "",
                item.urgency or "",
                item.booking_status or "",
                item.transfer_status or "",
                "yes" if item.recording_available else "no",
                item.processing_status,
            ]
        )
    return buffer.getvalue()


# --- Call detail ------------------------------------------------------------


class TranscriptTurn(BaseModel):
    turn_index: int
    role: str
    text: str | None
    started_at: datetime | None
    barge_in: bool
    interrupted: bool
    # Admin-only latency stages (None for clients)
    endpointing_ms: int | None = None
    stt_finalization_ms: int | None = None
    llm_ttft_ms: int | None = None
    tts_ttfb_ms: int | None = None
    total_latency_ms: int | None = None


class ToolExecutionView(BaseModel):
    tool_name: str
    status: str
    started_at: datetime
    duration_ms: int | None
    turn_id: uuid.UUID | None
    # Admin-only
    input_redacted: dict[str, Any] | None = None
    result_redacted: dict[str, Any] | None = None
    error_category: str | None = None


class GuardrailEventView(BaseModel):
    guardrail_type: str
    action: str
    created_at: datetime
    turn_id: uuid.UUID | None
    # Admin-only
    input_redacted: dict[str, Any] | None = None


class BookingView(BaseModel):
    id: uuid.UUID
    service: str | None
    scheduled_at: datetime
    timezone: str
    status: str
    customer_name: str | None


class MessageView(BaseModel):
    id: uuid.UUID
    customer_name: str | None
    urgency: str
    delivery_status: str
    created_at: datetime


class EscalationView(BaseModel):
    reason: str
    status: str
    initiated_at: datetime
    connected_at: datetime | None
    destination_last_four: str | None


class TimelineEntry(BaseModel):
    at: datetime
    label: str
    kind: str  # call | tool | guardrail | booking | message | escalation


class UsageEntry(BaseModel):
    usage_type: str
    quantity: int
    unit: str
    cost_cents: int | None
    provider: str | None = None  # admin-only


class CallDetail(BaseModel):
    id: uuid.UUID
    started_at: datetime
    answered_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    direction: str
    transport: str
    from_number_last_four: str | None
    to_number: str
    outcome: str | None
    urgency: str | None
    recording_available: bool
    transcript_status: str
    processing_status: str
    estimated_cost_cents: int | None
    summary: str | None
    sentiment: str | None
    follow_up_required: bool | None
    turns: list[TranscriptTurn]
    tools: list[ToolExecutionView]
    guardrails: list[GuardrailEventView]
    booking: BookingView | None
    message: MessageView | None
    escalation: EscalationView | None
    timeline: list[TimelineEntry]
    usage: list[UsageEntry]
    # Admin-only expansion
    provider_call_sid: str | None = None
    recording_status: str | None = None
    failure_category: str | None = None
    failure_detail_safe: str | None = None


async def _post_call_summary(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(AuditLog.after_redacted)
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.action == "post_call.processed",
                AuditLog.resource_id == str(call_id),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row or {}


def _timeline(
    call: Call,
    tools: list[ToolExecution],
    guardrails: list[GuardrailEvent],
    booking: Booking | None,
    message: Message | None,
    escalation: Escalation | None,
) -> list[TimelineEntry]:
    entries = [TimelineEntry(at=call.started_at, label="Call started", kind="call")]
    if call.answered_at:
        entries.append(TimelineEntry(at=call.answered_at, label="Answered", kind="call"))
    for tool in tools:
        entries.append(
            TimelineEntry(
                at=tool.started_at,
                label=f"{tool.tool_name.replace('_', ' ')} ({tool.status.value})",
                kind="tool",
            )
        )
    for event in guardrails:
        entries.append(
            TimelineEntry(
                at=event.created_at,
                label=f"Guardrail: {event.guardrail_type.value.replace('_', ' ')}",
                kind="guardrail",
            )
        )
    if booking:
        entries.append(
            TimelineEntry(
                at=booking.created_at,
                label=f"Booking {booking.status.value}",
                kind="booking",
            )
        )
    if message:
        entries.append(TimelineEntry(at=message.created_at, label="Message taken", kind="message"))
    if escalation:
        entries.append(
            TimelineEntry(
                at=escalation.initiated_at,
                label=f"Transfer {escalation.status.value.replace('_', ' ')}",
                kind="escalation",
            )
        )
    if call.ended_at:
        entries.append(TimelineEntry(at=call.ended_at, label="Call ended", kind="call"))
    entries.sort(key=lambda e: e.at)
    return entries


async def call_detail(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    *,
    admin: bool = False,
) -> CallDetail | None:
    call = (
        await session.execute(select(Call).where(Call.id == call_id, Call.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if call is None:
        return None

    turns = (
        (
            await session.execute(
                select(Turn)
                .where(Turn.call_id == call_id, Turn.tenant_id == tenant_id)
                .order_by(Turn.turn_index)
            )
        )
        .scalars()
        .all()
    )
    tools = (
        (
            await session.execute(
                select(ToolExecution)
                .where(ToolExecution.call_id == call_id, ToolExecution.tenant_id == tenant_id)
                .order_by(ToolExecution.started_at)
            )
        )
        .scalars()
        .all()
    )
    guardrails = (
        (
            await session.execute(
                select(GuardrailEvent)
                .where(
                    GuardrailEvent.call_id == call_id,
                    GuardrailEvent.tenant_id == tenant_id,
                )
                .order_by(GuardrailEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    bookings, escalations, service_names = await _related_by_call(session, tenant_id, [call_id])
    booking = bookings.get(call_id)
    escalation = escalations.get(call_id)
    message = (
        await session.execute(
            select(Message)
            .where(Message.call_id == call_id, Message.tenant_id == tenant_id)
            .order_by(Message.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    usage_rows = (
        (
            await session.execute(
                select(UsageRecord)
                .where(UsageRecord.call_id == call_id, UsageRecord.tenant_id == tenant_id)
                .order_by(UsageRecord.usage_type)
            )
        )
        .scalars()
        .all()
    )
    extraction = await _post_call_summary(session, tenant_id, call_id)

    return CallDetail(
        id=call.id,
        started_at=call.started_at,
        answered_at=call.answered_at,
        ended_at=call.ended_at,
        duration_seconds=call.duration_seconds,
        direction=call.direction.value,
        transport=call.transport.value,
        from_number_last_four=call.from_number_last_four,
        to_number=call.to_number,
        outcome=call.outcome.value if call.outcome else None,
        urgency=call.urgency.value if call.urgency else None,
        recording_available=call.recording_status is RecordingStatus.STORED,
        transcript_status=call.transcript_status.value,
        processing_status=call.post_processing_status.value,
        estimated_cost_cents=call.estimated_cost_cents,
        summary=extraction.get("summary"),
        sentiment=extraction.get("sentiment"),
        follow_up_required=extraction.get("follow_up_required"),
        turns=[
            TranscriptTurn(
                turn_index=t.turn_index,
                role=t.role.value,
                text=t.text,
                started_at=t.started_at,
                barge_in=t.barge_in,
                interrupted=t.interrupted,
                endpointing_ms=t.endpointing_ms if admin else None,
                stt_finalization_ms=t.stt_finalization_ms if admin else None,
                llm_ttft_ms=t.llm_ttft_ms if admin else None,
                tts_ttfb_ms=t.tts_ttfb_ms if admin else None,
                total_latency_ms=t.total_latency_ms if admin else None,
            )
            for t in turns
        ],
        tools=[
            ToolExecutionView(
                tool_name=t.tool_name,
                status=t.status.value,
                started_at=t.started_at,
                duration_ms=t.duration_ms,
                turn_id=t.turn_id,
                input_redacted=t.input_redacted if admin else None,
                result_redacted=t.result_redacted if admin else None,
                error_category=t.error_category if admin else None,
            )
            for t in tools
        ],
        guardrails=[
            GuardrailEventView(
                guardrail_type=g.guardrail_type.value,
                action=g.action.value,
                created_at=g.created_at,
                turn_id=g.turn_id,
                input_redacted=g.input_redacted if admin else None,
            )
            for g in guardrails
        ],
        booking=BookingView(
            id=booking.id,
            service=service_names.get(booking.service_id) if booking.service_id else None,
            scheduled_at=booking.scheduled_at,
            timezone=booking.timezone,
            status=booking.status.value,
            customer_name=booking.customer_name,
        )
        if booking
        else None,
        message=MessageView(
            id=message.id,
            customer_name=message.customer_name,
            urgency=message.urgency.value,
            delivery_status=message.delivery_status.value,
            created_at=message.created_at,
        )
        if message
        else None,
        escalation=EscalationView(
            reason=escalation.reason.value,
            status=escalation.status.value,
            initiated_at=escalation.initiated_at,
            connected_at=escalation.connected_at,
            destination_last_four=escalation.destination_last_four,
        )
        if escalation
        else None,
        timeline=_timeline(call, list(tools), list(guardrails), booking, message, escalation),
        usage=[
            UsageEntry(
                usage_type=u.usage_type,
                quantity=u.quantity,
                unit=u.unit,
                cost_cents=u.cost_cents,
                provider=u.provider if admin else None,
            )
            for u in usage_rows
        ],
        provider_call_sid=call.provider_call_sid if admin else None,
        recording_status=call.recording_status.value if admin else None,
        failure_category=call.failure_category if admin else None,
        failure_detail_safe=call.failure_detail_safe if admin else None,
    )
