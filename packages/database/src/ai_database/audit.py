"""Audit trail writer.

One function, used by every admin/config mutation. Values in
before/after must already be redacted by the caller — this layer never
sees raw sensitive data.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ai_database.models import AuditLog


async def write_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_external_user_id: str | None,
    actor_role: str | None,
    tenant_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        actor_external_user_id=actor_external_user_id,
        actor_role=actor_role,
        tenant_id=tenant_id,
        resource_type=resource_type,
        resource_id=resource_id,
        before_redacted=before,
        after_redacted=after,
        request_id=request_id,
    )
    session.add(entry)
    return entry
