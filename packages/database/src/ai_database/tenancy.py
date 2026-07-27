"""Tenant scoping for database sessions.

Two independent layers enforce isolation:

1. **Repositories** (``repositories.py``) — every tenant-owned query is
   built from a repository constructed with a ``tenant_id``; there is no
   unscoped code path.
2. **Row-level security** — policies on every tenant-owned table match
   ``current_setting('app.tenant_id')``. ``set_tenant_context`` issues a
   transaction-local ``SET LOCAL`` so a query that somehow escapes the
   repository layer still returns zero foreign rows when the service
   connects under the RLS-enforced application role.

The GUC is transaction-local (``SET LOCAL``) so pooled connections never
leak a tenant across checkouts.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_UUID_ERROR = "tenant_id must be a UUID"


async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Bind the current transaction to one tenant for RLS evaluation."""
    if not isinstance(tenant_id, uuid.UUID):  # defense against raw strings
        raise TypeError(_UUID_ERROR)
    # set_config with is_local=true == SET LOCAL; parameterized, no SQL injection.
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


async def clear_tenant_context(session: AsyncSession) -> None:
    """Reset the tenant GUC (defensive; SET LOCAL already ends with the tx)."""
    await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
