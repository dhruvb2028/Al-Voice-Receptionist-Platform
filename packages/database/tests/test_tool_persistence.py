"""SQL tool-persistence tests: idempotency, encryption at rest,
tenant isolation, reconciliation marking."""

import base64
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from ai_database.models import Booking, Message
from ai_database.tool_persistence import SqlBookingPersistence, SqlMessagePersistence
from ai_shared.crypto import AesGcmEncryptionService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime.now(UTC)


def _crypto() -> AesGcmEncryptionService:
    return AesGcmEncryptionService(
        data_key_b64=base64.b64encode(os.urandom(32)).decode(),
        hash_key_b64=base64.b64encode(os.urandom(32)).decode(),
    )


def _booking_kwargs() -> dict[str, Any]:
    return {
        "service_name": "drain cleaning",
        "slot_start": NOW,
        "slot_end": NOW,
        "customer_name": "Pat",
        "customer_phone": "+1 (555) 000-1111",
        "address": "1 Main St, Boston",
        "notes": "gate code 1234",
        "timezone": "America/New_York",
    }


async def test_booking_roundtrip_encrypts_and_confirms(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, _ = two_tenants
    crypto = _crypto()
    persistence = SqlBookingPersistence(db_session, tenant_id=tenant_a, call_id=None, crypto=crypto)

    record = await persistence.create_pending(
        idempotency_key=f"key-{uuid.uuid4().hex[:8]}", **_booking_kwargs()
    )
    assert record.status == "pending"
    assert record.already_existed is False

    await persistence.confirm(booking_id=record.booking_id, calendar_event_id="evt_1")

    row = (
        await db_session.execute(select(Booking).where(Booking.id == uuid.UUID(record.booking_id)))
    ).scalar_one()
    assert row.status.value == "confirmed"
    assert row.external_calendar_event_id == "evt_1"
    # Sensitive values are encrypted at rest; only fragments are readable.
    assert row.customer_phone_encrypted is not None
    assert "555" not in row.customer_phone_encrypted
    assert row.customer_phone_last_four == "1111"
    assert crypto.decrypt(row.customer_phone_encrypted) == "+15550001111"
    assert row.address_encrypted is not None
    assert "Main St" not in row.address_encrypted


async def test_booking_duplicate_key_returns_existing(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, _ = two_tenants
    persistence = SqlBookingPersistence(
        db_session, tenant_id=tenant_a, call_id=None, crypto=_crypto()
    )
    key = f"key-{uuid.uuid4().hex[:8]}"
    first = await persistence.create_pending(idempotency_key=key, **_booking_kwargs())
    await persistence.confirm(booking_id=first.booking_id, calendar_event_id="evt_1")

    replay = await persistence.create_pending(idempotency_key=key, **_booking_kwargs())
    assert replay.already_existed is True
    assert replay.booking_id == first.booking_id
    assert replay.status == "confirmed"


async def test_booking_confirm_is_tenant_scoped(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, tenant_b = two_tenants
    crypto = _crypto()
    persistence_a = SqlBookingPersistence(
        db_session, tenant_id=tenant_a, call_id=None, crypto=crypto
    )
    record = await persistence_a.create_pending(
        idempotency_key=f"key-{uuid.uuid4().hex[:8]}", **_booking_kwargs()
    )

    # Tenant B's persistence cannot touch tenant A's booking.
    persistence_b = SqlBookingPersistence(
        db_session, tenant_id=tenant_b, call_id=None, crypto=crypto
    )
    with pytest.raises(LookupError):
        await persistence_b.confirm(booking_id=record.booking_id, calendar_event_id="evt_x")


async def test_booking_reconciliation_marked(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, _ = two_tenants
    persistence = SqlBookingPersistence(
        db_session, tenant_id=tenant_a, call_id=None, crypto=_crypto()
    )
    record = await persistence.create_pending(
        idempotency_key=f"key-{uuid.uuid4().hex[:8]}", **_booking_kwargs()
    )
    await persistence.mark_reconciliation_required(
        booking_id=record.booking_id, calendar_event_id="evt_orphan"
    )
    row = (
        await db_session.execute(select(Booking).where(Booking.id == uuid.UUID(record.booking_id)))
    ).scalar_one()
    assert row.status.value == "reconciliation_required"
    assert row.reconciliation_status.value == "pending"
    assert row.external_calendar_event_id == "evt_orphan"


async def test_message_saved_encrypted_with_urgency(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, _ = two_tenants
    crypto = _crypto()
    persistence = SqlMessagePersistence(db_session, tenant_id=tenant_a, call_id=None, crypto=crypto)
    message_id = await persistence.save_message(
        customer_name="Sam",
        customer_phone="+15550002222",
        problem="water heater making noises",
        urgency="urgent",
        preferred_contact_time="after 5 PM",
        original_question="do you install heat pumps?",
    )
    row = (
        await db_session.execute(select(Message).where(Message.id == uuid.UUID(message_id)))
    ).scalar_one()
    assert row.urgency.value == "urgent"
    assert row.customer_phone_last_four == "2222"
    body = crypto.decrypt(row.body_encrypted)
    assert "water heater" in body
    assert "after 5 PM" in body
    assert "heat pumps" in body
    assert "water heater" not in row.body_encrypted


async def test_booking_concurrent_same_key_creates_exactly_one_row(
    migrated_database: str,
) -> None:
    """Two callers racing on the same idempotency key.

    The domain-level test covers this with mocks; this one runs the race
    against a real database on separate connections, which is where the
    unique constraint actually decides the winner. Without it, a retried
    webhook could double-book a slot.

    The tenant is committed rather than taken from the rolled-back
    ``db_session`` fixture — separate connections cannot see an
    uncommitted row, so the race would fail on a foreign key instead of
    on the constraint under test.
    """
    import asyncio

    from ai_database import create_engine, create_session_factory
    from ai_database.models import Tenant

    crypto = _crypto()
    key = f"race-{uuid.uuid4().hex[:8]}"
    slug = f"race-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_database)
    factory = create_session_factory(engine)

    async with factory() as session, session.begin():
        tenant = Tenant(name="Race Tenant", slug=slug)
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id

    async def attempt() -> str:
        async with factory() as session, session.begin():
            persistence = SqlBookingPersistence(
                session, tenant_id=tenant_id, call_id=None, crypto=crypto
            )
            record = await persistence.create_pending(idempotency_key=key, **_booking_kwargs())
            return record.booking_id

    results = await asyncio.gather(*(attempt() for _ in range(4)), return_exceptions=True)

    try:
        async with factory() as session:
            rows = (
                (await session.execute(select(Booking).where(Booking.idempotency_key == key)))
                .scalars()
                .all()
            )

        # Exactly one booking exists, and every attempt that returned a
        # value points at it — a caller is never told "booked" about a
        # row that does not exist.
        assert len(rows) == 1
        booking_id = str(rows[0].id)
        returned = [r for r in results if isinstance(r, str)]
        assert returned, f"every attempt failed: {results}"
        assert set(returned) == {booking_id}
    finally:
        async with factory() as session, session.begin():
            await session.execute(
                Booking.__table__.delete().where(Booking.idempotency_key == key)
            )
            await session.execute(Tenant.__table__.delete().where(Tenant.id == tenant_id))
        await engine.dispose()
