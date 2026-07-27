"""Platform-admin router.

A separate protected route group: every endpoint depends on
``require_platform_admin``, which 404s for non-admin principals so the
admin surface is invisible to client users. Admin endpoints select
tenants explicitly by ID — the only place that is permitted.
"""

import uuid
from typing import Annotated

from ai_database.repositories import AdminContext, AdminRepository
from ai_shared.errors import NotFoundError
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import require_platform_admin
from api.db import get_session

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminTenantView(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    vertical: str


def _repo(
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminRepository:
    return AdminRepository(session, context)


@router.get("/tenants")
async def list_tenants(
    repo: Annotated[AdminRepository, Depends(_repo)],
) -> list[AdminTenantView]:
    tenants = await repo.list_tenants()
    return [
        AdminTenantView(
            id=t.id, name=t.name, slug=t.slug, status=t.status.value, vertical=t.vertical
        )
        for t in tenants
    ]


@router.get("/tenants/{tenant_id}")
async def read_tenant(
    tenant_id: uuid.UUID,
    repo: Annotated[AdminRepository, Depends(_repo)],
) -> AdminTenantView:
    tenant = await repo.get_tenant(tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found.")
    return AdminTenantView(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status.value,
        vertical=tenant.vertical,
    )
