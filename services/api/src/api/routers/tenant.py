"""Client-facing tenant endpoints.

Tenant scope is derived exclusively from the verified principal — no
tenant ID is ever read from the URL or body on these routes. The calls
endpoint demonstrates the isolation contract: cross-tenant IDs yield
404, indistinguishable from nonexistent.
"""

import uuid
from datetime import datetime
from typing import Annotated

from ai_database.enums import BookingStatus, CallOutcome, Urgency
from ai_database.models import Tenant
from ai_shared.crypto import AesGcmEncryptionService, EncryptionService
from ai_shared.errors import NotFoundError, ValidationFailedError
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import require_client, require_client_owner
from api.auth.models import Principal
from api.db import get_session
from api.services.calls import (
    CallDetail,
    CallListFilters,
    CallListPage,
    call_detail,
    export_calls_csv,
    list_calls,
)
from api.services.client_records import (
    BookingListFilters,
    BookingListPage,
    MessageListFilters,
    MessageListItem,
    MessageListPage,
    cancel_booking,
    export_bookings_csv,
    export_messages_csv,
    list_bookings,
    list_messages,
    message_item,
    set_message_note,
    set_message_reviewed,
)
from api.services.metrics import TenantOverview, tenant_overview
from api.settings import get_settings

router = APIRouter(prefix="/tenant", tags=["tenant"])


class TenantView(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    timezone: str


def call_filters(
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    outcome: CallOutcome | None = None,
    urgency: Urgency | None = None,
    booking: str | None = Query(default=None, pattern="^(confirmed|pending|none)$"),
    sort: str = Query(default="-started_at", pattern="^-?(started_at|duration)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> CallListFilters:
    if date_from and date_to and date_from > date_to:
        raise ValidationFailedError("date_from must not be after date_to.")
    return CallListFilters(
        search=search,
        date_from=date_from,
        date_to=date_to,
        outcome=outcome,
        urgency=urgency,
        booking=booking,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("")
async def read_own_tenant(
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantView:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == principal.tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Not found.")
    return TenantView(
        id=tenant.id, name=tenant.name, status=tenant.status.value, timezone=tenant.timezone
    )


@router.get("/calls")
async def read_calls(
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    filters: Annotated[CallListFilters, Depends(call_filters)],
) -> CallListPage:
    """Call history with search, filters, sorting, and pagination."""
    assert principal.tenant_id is not None
    return await list_calls(session, principal.tenant_id, filters)


@router.get("/calls/export")
async def export_calls(
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    filters: Annotated[CallListFilters, Depends(call_filters)],
) -> PlainTextResponse:
    """CSV export of the filtered call history."""
    assert principal.tenant_id is not None
    body = await export_calls_csv(session, principal.tenant_id, filters)
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="calls.csv"'},
    )


@router.get("/calls/{call_id}")
async def read_call(
    call_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CallDetail:
    """Full call detail: overview, transcript, related records, timeline.

    Client view — internal identifiers and latency stages stay hidden.
    """
    assert principal.tenant_id is not None
    detail = await call_detail(session, principal.tenant_id, call_id)
    if detail is None:
        # Cross-tenant or nonexistent — the response is identical.
        raise NotFoundError("Not found.")
    return detail


class ConfigurationView(BaseModel):
    greeting: str | None
    business_phone: str | None
    address: str | None
    website: str | None
    timezone: str | None
    services: list[dict[str, object]]
    hours: list[dict[str, object]]
    configuration_version: int | None


@router.get("/configuration")
async def read_configuration(
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigurationView:
    """Read-only view of the active configuration (owner and staff).

    Change requests go through the account manager in v1; nothing here
    is writable from the client dashboard.
    """
    from ai_database.models import BusinessHours, Service, TenantConfig

    config = (
        await session.execute(
            select(TenantConfig).where(TenantConfig.tenant_id == principal.tenant_id)
        )
    ).scalar_one_or_none()
    services = (
        (
            await session.execute(
                select(Service)
                .where(Service.tenant_id == principal.tenant_id, Service.active.is_(True))
                .order_by(Service.name)
            )
        )
        .scalars()
        .all()
    )
    hours = (
        (
            await session.execute(
                select(BusinessHours)
                .where(BusinessHours.tenant_id == principal.tenant_id)
                .order_by(BusinessHours.weekday)
            )
        )
        .scalars()
        .all()
    )
    return ConfigurationView(
        greeting=config.greeting if config else None,
        business_phone=config.business_phone if config else None,
        address=config.address if config else None,
        website=config.website if config else None,
        timezone=config.timezone if config else None,
        services=[
            {
                "name": s.name,
                "description": s.description,
                "duration_minutes": s.duration_minutes,
                "category": s.category,
            }
            for s in services
        ],
        hours=[
            {
                "weekday": h.weekday,
                "closed": h.closed,
                "opens_at": h.opens_at.isoformat() if h.opens_at else None,
                "closes_at": h.closes_at.isoformat() if h.closes_at else None,
            }
            for h in hours
        ],
        configuration_version=config.configuration_version if config else None,
    )


class RecordingUrlResponse(BaseModel):
    url: str
    expires_seconds: int


@router.get("/calls/{call_id}/recording-url")
async def recording_url(
    call_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RecordingUrlResponse:
    """Short-lived signed URL for a call recording (owner and staff).

    Authorization is tenant-scoped; access is audited. No permanent
    public URLs exist anywhere in the platform.
    """
    from api.services.recordings import get_storage, sign_recording_url

    assert principal.tenant_id is not None
    url = await sign_recording_url(
        session,
        call_id=call_id,
        tenant_id=principal.tenant_id,
        storage=get_storage(),
        actor_external_user_id=principal.external_user_id,
        actor_role=principal.role.value,
    )
    if url is None:
        raise NotFoundError("Not found.")
    return RecordingUrlResponse(url=url, expires_seconds=900)


@router.get("/overview")
async def read_overview(
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    window_days: int = Query(default=30, ge=1, le=365),
) -> TenantOverview:
    """Headline metrics and chart series for the tenant dashboard."""
    assert principal.tenant_id is not None
    return await tenant_overview(session, principal.tenant_id, window_days=window_days)


@router.get("/usage")
async def read_usage(
    principal: Annotated[Principal, Depends(require_client_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
    window_days: int = Query(default=30, ge=1, le=365),
) -> TenantOverview:
    """Owner-only usage view — same computation, narrower audience."""
    assert principal.tenant_id is not None
    return await tenant_overview(session, principal.tenant_id, window_days=window_days)


# --- Bookings and messages ---------------------------------------------------


def _optional_crypto() -> EncryptionService | None:
    """None when encryption keys are unconfigured (local dev) — encrypted
    fields then render as unavailable rather than erroring."""
    settings = get_settings()
    if not settings.data_encryption_key or not settings.lookup_hash_key:
        return None
    return AesGcmEncryptionService(
        data_key_b64=settings.data_encryption_key,
        hash_key_b64=settings.lookup_hash_key,
    )


def booking_filters(
    search: str | None = None,
    status: BookingStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: str = Query(default="-scheduled_at", pattern="^-?scheduled_at$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> BookingListFilters:
    return BookingListFilters(
        search=search,
        status=status,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("/bookings")
async def read_bookings(
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    filters: Annotated[BookingListFilters, Depends(booking_filters)],
) -> BookingListPage:
    """Bookings with search, status and date filters, and pagination."""
    assert principal.tenant_id is not None
    return await list_bookings(session, principal.tenant_id, filters, _optional_crypto())


@router.get("/bookings/export")
async def export_bookings(
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    filters: Annotated[BookingListFilters, Depends(booking_filters)],
) -> PlainTextResponse:
    """CSV export of the filtered bookings."""
    assert principal.tenant_id is not None
    body = await export_bookings_csv(session, principal.tenant_id, filters, _optional_crypto())
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="bookings.csv"'},
    )


class CancelBookingRequest(BaseModel):
    confirm: bool = False


class BookingActionResponse(BaseModel):
    id: uuid.UUID
    status: str
    reconciliation_status: str


@router.post("/bookings/{booking_id}/cancel")
async def request_booking_cancellation(
    booking_id: uuid.UUID,
    request: CancelBookingRequest,
    principal: Annotated[Principal, Depends(require_client_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BookingActionResponse:
    """Owner-only cancellation with explicit confirmation. Audited, and
    never a hard delete — calendar cleanup goes through reconciliation."""
    if not request.confirm:
        raise ValidationFailedError("Cancellation requires explicit confirmation.")
    assert principal.tenant_id is not None
    booking = await cancel_booking(
        session,
        tenant_id=principal.tenant_id,
        booking_id=booking_id,
        actor_external_user_id=principal.external_user_id,
        actor_role=principal.role.value,
    )
    if booking is None:
        raise NotFoundError("Not found.")
    return BookingActionResponse(
        id=booking.id,
        status=booking.status.value,
        reconciliation_status=booking.reconciliation_status.value,
    )


def message_filters(
    search: str | None = None,
    urgency: Urgency | None = None,
    reviewed: str | None = Query(default=None, pattern="^(yes|no)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> MessageListFilters:
    return MessageListFilters(
        search=search, urgency=urgency, reviewed=reviewed, page=page, page_size=page_size
    )


@router.get("/messages")
async def read_messages(
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    filters: Annotated[MessageListFilters, Depends(message_filters)],
) -> MessageListPage:
    """Messages with urgency and reviewed filters, and pagination."""
    assert principal.tenant_id is not None
    return await list_messages(session, principal.tenant_id, filters, _optional_crypto())


@router.get("/messages/export")
async def export_messages(
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    filters: Annotated[MessageListFilters, Depends(message_filters)],
) -> PlainTextResponse:
    """CSV export of the filtered messages."""
    assert principal.tenant_id is not None
    body = await export_messages_csv(session, principal.tenant_id, filters, _optional_crypto())
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="messages.csv"'},
    )


class ReviewMessageRequest(BaseModel):
    reviewed: bool


@router.post("/messages/{message_id}/review")
async def review_message(
    message_id: uuid.UUID,
    request: ReviewMessageRequest,
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MessageListItem:
    """Mark a message reviewed (or not); audited."""
    assert principal.tenant_id is not None
    message = await set_message_reviewed(
        session,
        tenant_id=principal.tenant_id,
        message_id=message_id,
        reviewed=request.reviewed,
        actor_external_user_id=principal.external_user_id,
        actor_role=principal.role.value,
    )
    if message is None:
        raise NotFoundError("Not found.")
    return message_item(message, _optional_crypto())


class MessageNoteRequest(BaseModel):
    note: str = Field(max_length=2000)


@router.put("/messages/{message_id}/note")
async def update_message_note(
    message_id: uuid.UUID,
    request: MessageNoteRequest,
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MessageListItem:
    """Set or clear the internal note. Stored encrypted, dashboard-only —
    the receptionist can never read it aloud."""
    assert principal.tenant_id is not None
    crypto = _optional_crypto()
    if crypto is None:
        raise ValidationFailedError("Encryption keys are not configured.")
    message = await set_message_note(
        session,
        tenant_id=principal.tenant_id,
        message_id=message_id,
        note=request.note.strip(),
        crypto=crypto,
        actor_external_user_id=principal.external_user_id,
        actor_role=principal.role.value,
    )
    if message is None:
        raise NotFoundError("Not found.")
    return message_item(message, crypto)
