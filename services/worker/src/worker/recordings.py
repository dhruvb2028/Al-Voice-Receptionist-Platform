"""Recording lifecycle: ingest to R2, signed access, retention sweeps.

Flow: fetch provider recording (tenant ownership verified against the
call row) → store in a tenant-prefixed R2 key → persist only the object
key → delete the provider copy when configured. Access is exclusively
via short-lived signed URLs minted after authorization, and every
deletion or admin access lands in the audit log.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from ai_database.audit import write_audit
from ai_database.enums import RecordingStatus
from ai_database.models import Call, TenantConfig
from ai_providers.errors import ProviderError
from ai_providers.storage import StorageProvider
from ai_providers.telephony import TelephonyProvider
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

DEFAULT_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 90
#: WAV files start with RIFF; anything else (or tiny bodies) is corrupt.
MIN_RECORDING_BYTES = 1024


def _recording_key(tenant_id: uuid.UUID, call_id: uuid.UUID) -> str:
    return f"tenants/{tenant_id}/calls/{call_id}/recording.wav"


def resolve_retention_days(config: TenantConfig | None) -> int:
    days = (config.recording_retention_days if config else None) or DEFAULT_RETENTION_DAYS
    return min(days, MAX_RETENTION_DAYS)


async def ingest_recording(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    storage: StorageProvider,
    telephony: TelephonyProvider,
    download: httpx.AsyncClient,
    delete_provider_copy: bool = True,
) -> RecordingStatus:
    """Move one call's recording from the provider into R2."""
    call = (await session.execute(select(Call).where(Call.id == call_id))).scalar_one_or_none()
    if call is None:
        raise LookupError("call not found")
    if call.recording_status not in (
        RecordingStatus.IN_PROGRESS,
        RecordingStatus.PENDING_FETCH,
        RecordingStatus.FAILED,  # retry path
    ):
        return call.recording_status

    metadata = await telephony.fetch_recording(call_sid=call.provider_call_sid)
    if metadata is None:
        # Not ready yet — QStash redelivery retries later.
        call.recording_status = RecordingStatus.PENDING_FETCH
        logger.info("recording_not_ready", call_id=str(call_id))
        return call.recording_status

    try:
        response = await download.get(metadata.download_url)
        response.raise_for_status()
        body = response.content
    except httpx.HTTPError as exc:
        call.recording_status = RecordingStatus.FAILED
        logger.warning("recording_download_failed", call_id=str(call_id), error=str(exc))
        return call.recording_status

    # Corrupt-object guard: real WAV audio only.
    if len(body) < MIN_RECORDING_BYTES or not body.startswith(b"RIFF"):
        call.recording_status = RecordingStatus.FAILED
        logger.warning("recording_corrupt", call_id=str(call_id), size=len(body))
        return call.recording_status

    config = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == call.tenant_id))
    ).scalar_one_or_none()
    key = _recording_key(call.tenant_id, call.id)
    try:
        await storage.upload(
            key=key,
            data=body,
            content_type="audio/wav",
            retain_days=resolve_retention_days(config),
        )
    except ProviderError as exc:
        call.recording_status = RecordingStatus.FAILED
        logger.warning("recording_upload_failed", call_id=str(call_id), error=exc.category)
        return call.recording_status

    # Only the object key touches PostgreSQL — never audio, never URLs.
    call.recording_object_key = key
    call.recording_status = RecordingStatus.STORED

    if delete_provider_copy:
        try:
            await _delete_provider_copy(telephony, metadata.recording_sid)
        except ProviderError:
            # Non-fatal: a scheduled sweep retries provider deletions.
            logger.warning("provider_copy_delete_failed", call_id=str(call_id))

    await session.flush()
    logger.info("recording_stored", call_id=str(call_id))
    return call.recording_status


async def _delete_provider_copy(telephony: TelephonyProvider, recording_sid: str) -> None:
    delete = getattr(telephony, "delete_recording", None)
    if delete is not None:
        await delete(recording_sid=recording_sid)


async def retention_sweep(
    session: AsyncSession,
    *,
    storage: StorageProvider,
    now: datetime | None = None,
    batch_size: int = 100,
) -> int:
    """Delete recordings past their tenant's retention window.

    Legal holds are exempt. Every deletion writes an audit event; a
    failed R2 delete leaves the row untouched for the next sweep.
    """
    now = now or datetime.now(UTC)
    deleted = 0

    candidates = (
        (
            await session.execute(
                select(Call)
                .where(
                    Call.recording_status == RecordingStatus.STORED,
                    Call.recording_legal_hold.is_(False),
                    Call.ended_at.is_not(None),
                )
                .limit(batch_size * 5)
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return 0

    configs: dict[uuid.UUID, TenantConfig] = {
        c.tenant_id: c
        for c in (
            await session.execute(
                select(TenantConfig).where(
                    TenantConfig.tenant_id.in_({c.tenant_id for c in candidates})
                )
            )
        ).scalars()
    }

    for call in candidates:
        retention = resolve_retention_days(configs.get(call.tenant_id))
        assert call.ended_at is not None
        if call.ended_at + timedelta(days=retention) > now:
            continue
        if not call.recording_object_key:
            continue
        try:
            await storage.delete(key=call.recording_object_key)
        except ProviderError:
            logger.warning("retention_delete_failed", call_id=str(call.id))
            continue  # retried by the next sweep
        call.recording_status = RecordingStatus.DELETED
        call.recording_object_key = None
        await write_audit(
            session,
            action="recording.retention_deleted",
            actor_external_user_id=None,
            actor_role="retention_sweep",
            tenant_id=call.tenant_id,
            resource_type="call",
            resource_id=str(call.id),
            after={"retention_days": retention},
        )
        deleted += 1
        if deleted >= batch_size:
            break

    await session.flush()
    logger.info("retention_sweep_complete", deleted=deleted)
    return deleted


def build_r2_storage(settings: Any) -> StorageProvider:
    """Construct the R2 provider from worker/api settings."""
    from ai_providers.r2 import R2StorageProvider

    if not (
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and settings.r2_bucket
    ):
        raise RuntimeError("R2 storage is not configured.")
    return R2StorageProvider(
        account_id=settings.r2_account_id,
        access_key_id=settings.r2_access_key_id,
        secret_access_key=settings.r2_secret_access_key,
        bucket=settings.r2_bucket,
        endpoint=settings.r2_endpoint,
    )
