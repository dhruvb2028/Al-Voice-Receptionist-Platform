"""Client-facing tenant endpoints.

Tenant scope is derived exclusively from the verified principal — no
tenant ID is ever read from the URL or body on these routes. The calls
endpoint demonstrates the isolation contract: cross-tenant IDs yield
404, indistinguishable from nonexistent.
"""

import uuid
from typing import Annotated

from ai_database.models import Call, Tenant
from ai_database.repositories import TenantScopedRepository
from ai_shared.errors import NotFoundError
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import (
    get_tenant_repository,
    require_client,
    require_client_owner,
)
from api.auth.models import Principal
from api.db import get_session

router = APIRouter(prefix="/tenant", tags=["tenant"])


class TenantView(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    timezone: str


class CallSummary(BaseModel):
    id: uuid.UUID
    outcome: str | None
    from_number_last_four: str | None
    duration_seconds: int | None


class UsageView(BaseModel):
    tenant_id: uuid.UUID
    note: str


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


@router.get("/calls/{call_id}")
async def read_call(
    call_id: uuid.UUID,
    repo: Annotated[TenantScopedRepository, Depends(get_tenant_repository)],
) -> CallSummary:
    call = await repo.get_owned(Call, call_id)
    if call is None:
        # Cross-tenant or nonexistent — the response is identical.
        raise NotFoundError("Not found.")
    return CallSummary(
        id=call.id,
        outcome=call.outcome.value if call.outcome else None,
        from_number_last_four=call.from_number_last_four,
        duration_seconds=call.duration_seconds,
    )


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


@router.get("/usage")
async def read_usage(
    principal: Annotated[Principal, Depends(require_client_owner)],
) -> UsageView:
    # Owner-only surface; real aggregation arrives with the usage milestone.
    assert principal.tenant_id is not None
    return UsageView(tenant_id=principal.tenant_id, note="usage reporting arrives later")
