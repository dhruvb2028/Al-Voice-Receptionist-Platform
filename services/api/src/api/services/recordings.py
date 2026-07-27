"""Signed recording access for the API service.

Authorization happens before signing; every access is audited. The
storage provider is R2 in deployed environments and injectable in
tests.
"""

import uuid

import structlog
from ai_database.audit import write_audit
from ai_database.enums import RecordingStatus
from ai_database.models import Call
from ai_providers.storage import StorageProvider
from ai_shared.errors import ValidationFailedError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.settings import get_settings

logger = structlog.get_logger()

_storage: StorageProvider | None = None


def get_storage() -> StorageProvider:
    global _storage
    if _storage is None:
        settings = get_settings()
        if not (
            settings.r2_account_id
            and settings.r2_access_key_id
            and settings.r2_secret_access_key
            and settings.r2_bucket
        ):
            raise ValidationFailedError("Recording storage is not configured.")
        from ai_providers.r2 import R2StorageProvider

        _storage = R2StorageProvider(
            account_id=settings.r2_account_id,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket=settings.r2_bucket,
            endpoint=settings.r2_endpoint,
        )
    return _storage


def set_storage(storage: StorageProvider | None) -> None:
    """Test hook."""
    global _storage
    _storage = storage


async def sign_recording_url(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    tenant_id: uuid.UUID,
    storage: StorageProvider,
    actor_external_user_id: str,
    actor_role: str,
    request_id: str | None = None,
    expires_seconds: int = 900,
) -> str | None:
    """Authorize, sign, and audit one recording access."""
    call = (
        await session.execute(select(Call).where(Call.id == call_id, Call.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if call is None or not call.recording_object_key:
        return None
    if call.recording_status is not RecordingStatus.STORED:
        return None

    signed = await storage.signed_url(
        key=call.recording_object_key, expires_seconds=expires_seconds
    )
    await write_audit(
        session,
        action="recording.accessed",
        actor_external_user_id=actor_external_user_id,
        actor_role=actor_role,
        tenant_id=tenant_id,
        resource_type="call",
        resource_id=str(call_id),
        after={"expires_seconds": expires_seconds},
        request_id=request_id,
    )
    await session.flush()
    return signed.url
