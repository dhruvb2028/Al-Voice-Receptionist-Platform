"""Notification plumbing for post-call jobs.

The full notification system arrives with its own milestone; this module
resolves the tenant's notification address and holds the email-provider
singleton (None until Resend is wired, so jobs degrade gracefully).
"""

import uuid

from ai_database.models import Call, TenantConfig
from ai_providers.messaging import EmailProvider
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_email: EmailProvider | None = None


def get_email_provider() -> EmailProvider | None:
    return _email


def set_email_provider(provider: EmailProvider | None) -> None:
    """Test seam / startup wiring."""
    global _email
    _email = provider


async def resolve_notify_address(session: AsyncSession, *, call_id: uuid.UUID) -> str | None:
    """The tenant's configured notification email, if any."""
    row = (
        await session.execute(
            select(TenantConfig.notification_email)
            .join(Call, Call.tenant_id == TenantConfig.tenant_id)
            .where(Call.id == call_id)
        )
    ).scalar_one_or_none()
    return row or None
