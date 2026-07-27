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


@router.get("/usage")
async def read_usage(
    principal: Annotated[Principal, Depends(require_client_owner)],
) -> UsageView:
    # Owner-only surface; real aggregation arrives with the usage milestone.
    assert principal.tenant_id is not None
    return UsageView(tenant_id=principal.tenant_id, note="usage reporting arrives later")
