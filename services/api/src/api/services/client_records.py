"""Bookings and messages for the client dashboard.

Sensitive fields (addresses, message bodies, internal notes) live
encrypted in PostgreSQL; this module decrypts them for authorized
dashboard reads only. Cancellation never deletes anything — it moves the
booking to CANCELLED, flags calendar reconciliation when an event was
created, and writes an audit entry. The internal message note is a
dashboard-only column: the conversation engine's persistence layer never
selects it, so the receptionist can never read it aloud.
"""

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ai_database.audit import write_audit
from ai_database.enums import BookingStatus, ReconciliationStatus, Urgency
from ai_database.models import Booking, Message, Service
from ai_shared.crypto import EncryptionError, EncryptionService
from ai_shared.errors import ConflictError
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_PAGE_SIZE = 100
EXPORT_ROW_CAP = 1000


def _decrypt(crypto: EncryptionService | None, ciphertext: str | None) -> str | None:
    if ciphertext is None or crypto is None:
        return None
    try:
        return crypto.decrypt(ciphertext)
    except EncryptionError:
        # Old-key or corrupt rows degrade to hidden, never to an error page.
        return None


# --- Bookings ---------------------------------------------------------------


@dataclass(frozen=True)
class BookingListFilters:
    search: str | None = None
    status: BookingStatus | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    sort: str = "-scheduled_at"
    page: int = 1
    page_size: int = 25


class BookingListItem(BaseModel):
    id: uuid.UUID
    customer_name: str | None
    phone_last_four: str | None
    service: str | None
    scheduled_at: datetime
    timezone: str
    address: str | None
    calendar_status: str  # linked | not_linked
    status: str
    call_id: uuid.UUID | None
    created_at: datetime


class BookingListPage(BaseModel):
    items: list[BookingListItem]
    total: int
    page: int
    page_size: int


def _booking_filters(stmt: Select[Any], tenant_id: uuid.UUID, f: BookingListFilters) -> Select[Any]:
    stmt = stmt.where(Booking.tenant_id == tenant_id)
    if f.search:
        term = f.search.strip()
        if term.isdigit() and len(term) == 4:
            stmt = stmt.where(Booking.customer_phone_last_four == term)
        else:
            stmt = stmt.where(Booking.customer_name.ilike(f"%{term}%"))
    if f.status:
        stmt = stmt.where(Booking.status == f.status)
    if f.date_from:
        stmt = stmt.where(Booking.scheduled_at >= f.date_from)
    if f.date_to:
        stmt = stmt.where(Booking.scheduled_at <= f.date_to)
    return stmt


async def _booking_items(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    f: BookingListFilters,
    crypto: EncryptionService | None,
    *,
    limit: int,
    offset: int,
) -> list[BookingListItem]:
    order = Booking.scheduled_at.asc() if f.sort == "scheduled_at" else Booking.scheduled_at.desc()
    stmt = (
        _booking_filters(select(Booking), tenant_id, f)
        .order_by(order, Booking.id)
        .limit(limit)
        .offset(offset or None)
    )
    bookings = (await session.execute(stmt)).scalars().all()

    service_ids = {b.service_id for b in bookings if b.service_id is not None}
    names: dict[uuid.UUID, str] = {}
    if service_ids:
        for service in (
            (await session.execute(select(Service).where(Service.id.in_(service_ids))))
            .scalars()
            .all()
        ):
            names[service.id] = service.name

    return [
        BookingListItem(
            id=b.id,
            customer_name=b.customer_name,
            phone_last_four=b.customer_phone_last_four,
            service=names.get(b.service_id) if b.service_id else None,
            scheduled_at=b.scheduled_at,
            timezone=b.timezone,
            address=_decrypt(crypto, b.address_encrypted),
            calendar_status="linked" if b.external_calendar_event_id else "not_linked",
            status=b.status.value,
            call_id=b.call_id,
            created_at=b.created_at,
        )
        for b in bookings
    ]


async def list_bookings(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    f: BookingListFilters,
    crypto: EncryptionService | None,
) -> BookingListPage:
    page = max(1, f.page)
    page_size = min(max(1, f.page_size), MAX_PAGE_SIZE)
    total = (
        await session.execute(_booking_filters(select(func.count(Booking.id)), tenant_id, f))
    ).scalar_one()
    items = await _booking_items(
        session, tenant_id, f, crypto, limit=page_size, offset=(page - 1) * page_size
    )
    return BookingListPage(items=items, total=total, page=page, page_size=page_size)


async def export_bookings_csv(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    f: BookingListFilters,
    crypto: EncryptionService | None,
) -> str:
    items = await _booking_items(session, tenant_id, f, crypto, limit=EXPORT_ROW_CAP, offset=0)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "customer_name",
            "phone_last_four",
            "service",
            "scheduled_at",
            "timezone",
            "address",
            "calendar_status",
            "status",
            "created_at",
        ]
    )
    for item in items:
        writer.writerow(
            [
                item.customer_name or "",
                item.phone_last_four or "",
                item.service or "",
                item.scheduled_at.isoformat(),
                item.timezone,
                item.address or "",
                item.calendar_status,
                item.status,
                item.created_at.isoformat(),
            ]
        )
    return buffer.getvalue()


async def cancel_booking(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    actor_external_user_id: str | None,
    actor_role: str | None,
) -> Booking | None:
    """Authorized cancellation request: CANCELLED status plus calendar
    reconciliation when an event exists. Never a hard delete."""
    booking = (
        await session.execute(
            select(Booking).where(Booking.id == booking_id, Booking.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if booking is None:
        return None
    if booking.status is BookingStatus.CANCELLED:
        raise ConflictError("This booking is already cancelled.")

    before = booking.status.value
    booking.status = BookingStatus.CANCELLED
    if booking.external_calendar_event_id:
        # The calendar event is removed by the reconciliation worker, not
        # inline — a calendar outage must not block the cancellation.
        booking.reconciliation_status = ReconciliationStatus.PENDING
    await write_audit(
        session,
        action="booking.cancelled",
        actor_external_user_id=actor_external_user_id,
        actor_role=actor_role,
        tenant_id=tenant_id,
        resource_type="booking",
        resource_id=str(booking_id),
        before={"status": before},
        after={"status": booking.status.value},
    )
    await session.flush()
    return booking


# --- Messages ---------------------------------------------------------------


@dataclass(frozen=True)
class MessageListFilters:
    search: str | None = None
    urgency: Urgency | None = None
    reviewed: str | None = None  # yes | no
    page: int = 1
    page_size: int = 25


class MessageListItem(BaseModel):
    id: uuid.UUID
    customer_name: str | None
    phone_last_four: str | None
    body: str | None
    urgency: str
    created_at: datetime
    call_id: uuid.UUID | None
    delivery_status: str
    reviewed_at: datetime | None
    internal_note: str | None


class MessageListPage(BaseModel):
    items: list[MessageListItem]
    total: int
    page: int
    page_size: int


def _message_filters(stmt: Select[Any], tenant_id: uuid.UUID, f: MessageListFilters) -> Select[Any]:
    stmt = stmt.where(Message.tenant_id == tenant_id)
    if f.search:
        term = f.search.strip()
        if term.isdigit() and len(term) == 4:
            stmt = stmt.where(Message.customer_phone_last_four == term)
        else:
            stmt = stmt.where(Message.customer_name.ilike(f"%{term}%"))
    if f.urgency:
        stmt = stmt.where(Message.urgency == f.urgency)
    if f.reviewed == "yes":
        stmt = stmt.where(Message.reviewed_at.is_not(None))
    elif f.reviewed == "no":
        stmt = stmt.where(Message.reviewed_at.is_(None))
    return stmt


def message_item(message: Message, crypto: EncryptionService | None) -> MessageListItem:
    return MessageListItem(
        id=message.id,
        customer_name=message.customer_name,
        phone_last_four=message.customer_phone_last_four,
        body=_decrypt(crypto, message.body_encrypted),
        urgency=message.urgency.value,
        created_at=message.created_at,
        call_id=message.call_id,
        delivery_status=message.delivery_status.value,
        reviewed_at=message.reviewed_at,
        internal_note=_decrypt(crypto, message.internal_note_encrypted),
    )


async def list_messages(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    f: MessageListFilters,
    crypto: EncryptionService | None,
) -> MessageListPage:
    page = max(1, f.page)
    page_size = min(max(1, f.page_size), MAX_PAGE_SIZE)
    total = (
        await session.execute(_message_filters(select(func.count(Message.id)), tenant_id, f))
    ).scalar_one()
    messages = (
        (
            await session.execute(
                _message_filters(select(Message), tenant_id, f)
                .order_by(Message.created_at.desc(), Message.id)
                .limit(page_size)
                .offset((page - 1) * page_size or None)
            )
        )
        .scalars()
        .all()
    )
    return MessageListPage(
        items=[message_item(m, crypto) for m in messages],
        total=total,
        page=page,
        page_size=page_size,
    )


async def export_messages_csv(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    f: MessageListFilters,
    crypto: EncryptionService | None,
) -> str:
    messages = (
        (
            await session.execute(
                _message_filters(select(Message), tenant_id, f)
                .order_by(Message.created_at.desc(), Message.id)
                .limit(EXPORT_ROW_CAP)
            )
        )
        .scalars()
        .all()
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "customer_name",
            "phone_last_four",
            "message",
            "urgency",
            "received_at",
            "delivery_status",
            "reviewed",
        ]
    )
    for message in messages:
        item = message_item(message, crypto)
        writer.writerow(
            [
                item.customer_name or "",
                item.phone_last_four or "",
                item.body or "",
                item.urgency,
                item.created_at.isoformat(),
                item.delivery_status,
                "yes" if item.reviewed_at else "no",
            ]
        )
    return buffer.getvalue()


async def _owned_message(
    session: AsyncSession, tenant_id: uuid.UUID, message_id: uuid.UUID
) -> Message | None:
    return (
        await session.execute(
            select(Message).where(Message.id == message_id, Message.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()


async def set_message_reviewed(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
    reviewed: bool,
    actor_external_user_id: str | None,
    actor_role: str | None,
) -> Message | None:
    message = await _owned_message(session, tenant_id, message_id)
    if message is None:
        return None
    message.reviewed_at = datetime.now(UTC) if reviewed else None
    await write_audit(
        session,
        action="message.reviewed" if reviewed else "message.unreviewed",
        actor_external_user_id=actor_external_user_id,
        actor_role=actor_role,
        tenant_id=tenant_id,
        resource_type="message",
        resource_id=str(message_id),
    )
    await session.flush()
    return message


async def set_message_note(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
    note: str,
    crypto: EncryptionService,
    actor_external_user_id: str | None,
    actor_role: str | None,
) -> Message | None:
    """Store the internal note encrypted. The note text itself is never
    written to the audit log."""
    message = await _owned_message(session, tenant_id, message_id)
    if message is None:
        return None
    message.internal_note_encrypted = crypto.encrypt(note) if note else None
    await write_audit(
        session,
        action="message.note_updated",
        actor_external_user_id=actor_external_user_id,
        actor_role=actor_role,
        tenant_id=tenant_id,
        resource_type="message",
        resource_id=str(message_id),
        after={"has_note": bool(note)},
    )
    await session.flush()
    return message
