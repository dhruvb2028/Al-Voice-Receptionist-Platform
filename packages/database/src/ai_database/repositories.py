"""Tenant-scoped repository layer.

A repository is constructed with the ``tenant_id`` derived from the
authenticated request (never from a payload), and every query it builds
carries that scope. Client-facing code cannot express an unscoped query
because the scope is bound at construction, not passed per call.

Cross-tenant lookups return ``None``/empty — callers translate that to
404, never 403, so resource existence is not confirmed across tenants.

Admin access uses :class:`AdminRepository`, a deliberately separate type
requiring an :class:`AdminContext`, so a client code path cannot widen
itself by passing a flag.
"""

import uuid
from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_database.metadata import Base
from ai_database.models import Tenant
from ai_database.tenancy import set_tenant_context

TenantOwnedT = TypeVar("TenantOwnedT", bound=Base)


@dataclass(frozen=True)
class AdminContext:
    """Proof of platform-admin authorization; constructed only by the
    admin auth dependency after role verification. Carries the actor for
    audit attribution."""

    actor_external_user_id: str
    request_id: str | None = None


class TenantScopedRepository:
    """Base for all client-facing data access. Scope is fixed at
    construction and applied to every statement."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        if not isinstance(tenant_id, uuid.UUID):
            raise TypeError("tenant_id must be a UUID")
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> uuid.UUID:
        return self._tenant_id

    async def bind_rls(self) -> None:
        """Set the transaction-local RLS context. Called by the session
        dependency at transaction start."""
        await set_tenant_context(self._session, self._tenant_id)

    def _scoped(
        self, model: type[TenantOwnedT], stmt: Select[tuple[TenantOwnedT]]
    ) -> Select[tuple[TenantOwnedT]]:
        return stmt.where(model.tenant_id == self._tenant_id)  # type: ignore[attr-defined]

    async def get_owned(self, model: type[TenantOwnedT], row_id: uuid.UUID) -> TenantOwnedT | None:
        """Fetch one row if and only if it belongs to this tenant."""
        stmt = self._scoped(model, select(model).where(model.id == row_id))  # type: ignore[attr-defined]
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_owned(
        self, model: type[TenantOwnedT], *, limit: int = 50, offset: int = 0
    ) -> list[TenantOwnedT]:
        stmt = self._scoped(model, select(model)).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars())


class AdminRepository:
    """Cross-tenant access for the platform-admin surface only.

    Every instance requires an :class:`AdminContext`; call sites cannot
    reach cross-tenant data without having passed admin authorization.
    """

    def __init__(self, session: AsyncSession, context: AdminContext) -> None:
        self._session = session
        self._context = context

    @property
    def context(self) -> AdminContext:
        return self._context

    async def get_tenant(self, tenant_id: uuid.UUID) -> Tenant | None:
        return (
            await self._session.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()

    async def get_tenant_by_slug(self, slug: str) -> Tenant | None:
        return (
            await self._session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()

    async def list_tenants(self, *, limit: int = 100, offset: int = 0) -> list[Tenant]:
        stmt = select(Tenant).order_by(Tenant.created_at).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars())
