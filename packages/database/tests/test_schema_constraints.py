"""Schema constraint tests against real PostgreSQL.

Covers the acceptance list: booking idempotency, unique phone numbers,
unique provider call SIDs, tenant ownership, cross-tenant rejection,
enum validation, and safe deletion behaviour.
"""

import uuid
from datetime import UTC, datetime

import pytest
from ai_database.models import Booking, Call, PhoneNumber, Service, Tenant
from ai_database.repositories import TenantScopedRepository
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime.now(UTC)


def _call(tenant_id: uuid.UUID, sid: str) -> Call:
    return Call(
        tenant_id=tenant_id,
        provider_call_sid=sid,
        to_number="+15555550123",
        started_at=NOW,
    )


def _booking(tenant_id: uuid.UUID, key: str) -> Booking:
    return Booking(
        tenant_id=tenant_id,
        scheduled_at=NOW,
        timezone="America/New_York",
        idempotency_key=key,
    )


async def test_booking_idempotency_key_unique(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, tenant_b = two_tenants
    db_session.add(_booking(tenant_a, "idem-1"))
    await db_session.flush()
    # Globally unique: even another tenant cannot reuse the key.
    db_session.add(_booking(tenant_b, "idem-1"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_phone_number_globally_unique(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, tenant_b = two_tenants
    db_session.add(PhoneNumber(tenant_id=tenant_a, e164="+15555551234"))
    await db_session.flush()
    db_session.add(PhoneNumber(tenant_id=tenant_b, e164="+15555551234"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_phone_number_format_enforced(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, _ = two_tenants
    db_session.add(PhoneNumber(tenant_id=tenant_a, e164="not-a-number"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_provider_call_sid_unique(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, tenant_b = two_tenants
    db_session.add(_call(tenant_a, "CA_dup"))
    await db_session.flush()
    db_session.add(_call(tenant_b, "CA_dup"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_tenant_ownership_scoped_fetch(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, _ = two_tenants
    call = _call(tenant_a, "CA_owned")
    db_session.add(call)
    await db_session.flush()

    repo = TenantScopedRepository(db_session, tenant_a)
    found = await repo.get_owned(Call, call.id)
    assert found is not None
    assert found.id == call.id


async def test_cross_tenant_access_rejected(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, tenant_b = two_tenants
    call = _call(tenant_a, "CA_secret")
    db_session.add(call)
    await db_session.flush()

    # Tenant B's repository cannot see tenant A's call by ID.
    repo_b = TenantScopedRepository(db_session, tenant_b)
    assert await repo_b.get_owned(Call, call.id) is None
    assert await repo_b.list_owned(Call) == []


async def test_enum_values_validated(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, _ = two_tenants
    call = _call(tenant_a, "CA_enum")
    call.outcome = "made_up_outcome"  # type: ignore[assignment]
    db_session.add(call)
    with pytest.raises((DBAPIError, LookupError)):
        await db_session.flush()


async def test_service_name_unique_per_tenant_not_global(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_a, tenant_b = two_tenants
    db_session.add(
        Service(tenant_id=tenant_a, name="Drain Cleaning", name_normalized="drain cleaning")
    )
    # Same normalized name for a different tenant is fine.
    db_session.add(
        Service(tenant_id=tenant_b, name="Drain cleaning", name_normalized="drain cleaning")
    )
    await db_session.flush()
    # Duplicate within one tenant is rejected.
    db_session.add(
        Service(tenant_id=tenant_a, name="DRAIN CLEANING", name_normalized="drain cleaning")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_tenant_delete_restricted_by_call_history(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Call history blocks tenant deletion — history is never lost to a cascade."""
    tenant_a, _ = two_tenants
    db_session.add(_call(tenant_a, "CA_history"))
    await db_session.flush()

    tenant = (await db_session.execute(select(Tenant).where(Tenant.id == tenant_a))).scalar_one()
    await db_session.delete(tenant)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_config_cascades_with_tenant(
    db_session: AsyncSession, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Configuration (no history value) does cascade on tenant deletion."""
    from ai_database.models import TenantConfig

    _, tenant_b = two_tenants
    db_session.add(TenantConfig(tenant_id=tenant_b))
    await db_session.flush()

    tenant = (await db_session.execute(select(Tenant).where(Tenant.id == tenant_b))).scalar_one()
    await db_session.delete(tenant)
    await db_session.flush()  # no calls -> deletion allowed, config cascades

    remaining = (
        await db_session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_b))
    ).scalar_one_or_none()
    assert remaining is None
