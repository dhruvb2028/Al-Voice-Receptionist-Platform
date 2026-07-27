"""Database-backed persistence for the business tools.

Implements the ``BookingPersistence`` and ``MessagePersistence``
protocols from ``ai_domain.tools`` against the real schema. Sensitive
values are encrypted before they touch a row.
"""

import uuid
from datetime import datetime

import structlog
from ai_domain.tools import BookingRecord
from ai_shared.crypto import EncryptionService, last_four, normalize_phone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_database.enums import BookingStatus, ReconciliationStatus, Urgency
from ai_database.models import Booking, Message, Service

logger = structlog.get_logger()


class SqlBookingPersistence:
    def __init__(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        call_id: uuid.UUID | None,
        crypto: EncryptionService,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._call_id = call_id
        self._crypto = crypto

    async def _service_id(self, service_name: str) -> uuid.UUID | None:
        return (
            await self._session.execute(
                select(Service.id).where(
                    Service.tenant_id == self._tenant_id,
                    Service.name_normalized == service_name.strip().lower(),
                )
            )
        ).scalar_one_or_none()

    async def create_pending(
        self,
        *,
        idempotency_key: str,
        service_name: str,
        slot_start: datetime,
        slot_end: datetime,
        customer_name: str,
        customer_phone: str,
        address: str,
        notes: str | None,
        timezone: str,
    ) -> BookingRecord:
        phone = normalize_phone(customer_phone)
        booking = Booking(
            tenant_id=self._tenant_id,
            call_id=self._call_id,
            service_id=await self._service_id(service_name),
            customer_name=customer_name,
            customer_phone_encrypted=self._crypto.encrypt(phone),
            customer_phone_hash=self._crypto.hash_for_lookup(phone),
            customer_phone_last_four=last_four(phone),
            address_encrypted=self._crypto.encrypt(address),
            notes_encrypted=self._crypto.encrypt(notes) if notes else None,
            scheduled_at=slot_start,
            timezone=timezone,
            idempotency_key=idempotency_key,
            status=BookingStatus.PENDING,
        )
        try:
            # Savepoint: a duplicate key must not poison the caller's
            # transaction (the call is mid-conversation).
            async with self._session.begin_nested():
                self._session.add(booking)
                await self._session.flush()
        except IntegrityError:
            existing = (
                await self._session.execute(
                    select(Booking).where(Booking.idempotency_key == idempotency_key)
                )
            ).scalar_one_or_none()
            if existing is None or existing.tenant_id != self._tenant_id:
                # Key collision across tenants is not treated as a
                # duplicate — surface as failure.
                raise
            return BookingRecord(
                booking_id=str(existing.id),
                status=existing.status.value,  # type: ignore[arg-type]
                already_existed=True,
            )
        return BookingRecord(booking_id=str(booking.id), status="pending")

    async def confirm(self, *, booking_id: str, calendar_event_id: str) -> None:
        booking = await self._owned(booking_id)
        booking.status = BookingStatus.CONFIRMED
        booking.external_calendar_event_id = calendar_event_id
        await self._session.flush()

    async def mark_failed(self, *, booking_id: str) -> None:
        booking = await self._owned(booking_id)
        booking.status = BookingStatus.FAILED
        await self._session.flush()

    async def mark_reconciliation_required(
        self, *, booking_id: str, calendar_event_id: str | None
    ) -> None:
        booking = await self._owned(booking_id)
        booking.status = BookingStatus.RECONCILIATION_REQUIRED
        booking.reconciliation_status = ReconciliationStatus.PENDING
        booking.external_calendar_event_id = calendar_event_id
        await self._session.flush()

    async def _owned(self, booking_id: str) -> Booking:
        booking = (
            await self._session.execute(
                select(Booking).where(
                    Booking.id == uuid.UUID(booking_id),
                    Booking.tenant_id == self._tenant_id,
                )
            )
        ).scalar_one_or_none()
        if booking is None:
            raise LookupError("booking not found for tenant")
        return booking


class SqlMessagePersistence:
    def __init__(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        call_id: uuid.UUID | None,
        crypto: EncryptionService,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._call_id = call_id
        self._crypto = crypto

    async def save_message(
        self,
        *,
        customer_name: str | None,
        customer_phone: str,
        problem: str,
        urgency: str,
        preferred_contact_time: str | None,
        original_question: str | None,
    ) -> str:
        phone = normalize_phone(customer_phone)
        body_parts = [problem]
        if preferred_contact_time:
            body_parts.append(f"Preferred contact time: {preferred_contact_time}")
        if original_question:
            body_parts.append(f"Original question: {original_question}")

        message = Message(
            tenant_id=self._tenant_id,
            call_id=self._call_id,
            customer_name=customer_name,
            customer_phone_encrypted=self._crypto.encrypt(phone),
            customer_phone_last_four=last_four(phone),
            body_encrypted=self._crypto.encrypt("\n".join(body_parts)),
            urgency=Urgency(urgency),
        )
        self._session.add(message)
        await self._session.flush()
        return str(message.id)
