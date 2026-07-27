"""Configuration approval workflow.

States: draft → pending_review → active (previous active → superseded),
or → rejected. One open draft and one active version per tenant,
enforced by partial unique indexes.

Approval **applies** the validated payload onto the live config tables
(tenant_config, services, price_rules, business_hours,
holiday_overrides) in the same transaction that flips version states —
the voice path reads only those tables, so it can never observe a
draft. Rollback re-applies an older snapshot as a new approved version;
history is never rewritten.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any

import structlog
from ai_database.audit import write_audit
from ai_database.enums import ConfigVersionState
from ai_database.models import (
    BusinessHours,
    ConfigVersion,
    HolidayOverride,
    PriceRule,
    Service,
    Tenant,
    TenantConfig,
)
from ai_database.repositories import AdminContext
from ai_domain.config import ReceptionistConfig
from ai_shared.errors import ConflictError, NotFoundError, ValidationFailedError
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


async def _get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Tenant not found.")
    return tenant


def _validate_payload(payload: dict[str, Any]) -> ReceptionistConfig:
    try:
        return ReceptionistConfig.model_validate(payload)
    except ValidationError as exc:
        from ai_shared.errors import ErrorDetail

        details = [
            ErrorDetail(
                field=".".join(str(p) for p in error["loc"]),
                issue=error["msg"],
            )
            for error in exc.errors()[:20]
        ]
        raise ValidationFailedError("Configuration is invalid.", details=details) from exc


async def get_open_draft(session: AsyncSession, tenant_id: uuid.UUID) -> ConfigVersion | None:
    return (
        await session.execute(
            select(ConfigVersion).where(
                ConfigVersion.tenant_id == tenant_id,
                ConfigVersion.state.in_(
                    [ConfigVersionState.DRAFT, ConfigVersionState.PENDING_REVIEW]
                ),
            )
        )
    ).scalar_one_or_none()


async def get_active_version(session: AsyncSession, tenant_id: uuid.UUID) -> ConfigVersion | None:
    return (
        await session.execute(
            select(ConfigVersion).where(
                ConfigVersion.tenant_id == tenant_id,
                ConfigVersion.state == ConfigVersionState.ACTIVE,
            )
        )
    ).scalar_one_or_none()


async def save_draft(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    payload: dict[str, Any],
    context: AdminContext,
) -> ConfigVersion:
    """Create or update the tenant's single open draft (validated)."""
    await _get_tenant(session, tenant_id)
    _validate_payload(payload)

    draft = await get_open_draft(session, tenant_id)
    if draft is not None:
        if draft.state is ConfigVersionState.PENDING_REVIEW:
            raise ConflictError("A version is already pending review; approve or reject it first.")
        before_version = draft.version
        draft.payload = payload
        action = "config.draft_updated"
    else:
        max_version = (
            await session.execute(
                select(func.coalesce(func.max(ConfigVersion.version), 0)).where(
                    ConfigVersion.tenant_id == tenant_id
                )
            )
        ).scalar_one()
        draft = ConfigVersion(
            tenant_id=tenant_id,
            version=max_version + 1,
            state=ConfigVersionState.DRAFT,
            payload=payload,
            created_by=context.actor_external_user_id,
        )
        session.add(draft)
        before_version = None
        action = "config.draft_created"
    await session.flush()

    await write_audit(
        session,
        action=action,
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        tenant_id=tenant_id,
        resource_type="config_version",
        resource_id=str(draft.id),
        before={"version": before_version} if before_version else None,
        after={"version": draft.version},
        request_id=context.request_id,
    )
    return draft


async def submit_draft(
    session: AsyncSession, *, tenant_id: uuid.UUID, context: AdminContext
) -> ConfigVersion:
    draft = await get_open_draft(session, tenant_id)
    if draft is None or draft.state is not ConfigVersionState.DRAFT:
        raise NotFoundError("No draft to submit.")
    _validate_payload(draft.payload)
    draft.state = ConfigVersionState.PENDING_REVIEW
    draft.submitted_at = datetime.now(UTC)
    await write_audit(
        session,
        action="config.submitted_for_review",
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        tenant_id=tenant_id,
        resource_type="config_version",
        resource_id=str(draft.id),
        after={"version": draft.version},
        request_id=context.request_id,
    )
    return draft


async def _apply_to_live_tables(
    session: AsyncSession, tenant_id: uuid.UUID, config: ReceptionistConfig
) -> None:
    """Replace the live configuration tables with the approved payload."""
    tenant = await _get_tenant(session, tenant_id)
    tenant.name = config.identity.business_name
    tenant.timezone = config.identity.timezone

    tenant_config = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if tenant_config is None:
        tenant_config = TenantConfig(tenant_id=tenant_id)
        session.add(tenant_config)

    tenant_config.greeting = config.greeting.greeting
    tenant_config.recording_consent_text = config.greeting.recording_notice
    tenant_config.recording_enabled = bool(config.greeting.recording_notice)
    tenant_config.after_hours_greeting = config.greeting.after_hours_greeting
    tenant_config.timezone = config.identity.timezone
    tenant_config.business_phone = config.identity.business_phone
    tenant_config.address = config.identity.address
    tenant_config.website = config.identity.website
    tenant_config.escalation_number = config.escalation.emergency_destination
    tenant_config.escalation_policy = config.escalation.model_dump(mode="json")
    tenant_config.service_area = config.service_area.model_dump(mode="json")
    tenant_config.voice_id = config.voice.voice_id
    tenant_config.speaking_style = config.voice.speaking_style
    tenant_config.language = config.voice.language
    tenant_config.filler_phrases = {"phrases": config.voice.filler_phrases}
    tenant_config.max_call_seconds = config.voice.max_call_seconds
    tenant_config.configuration_version += 1

    # Services and prices: full replace. Bookings reference services with
    # SET NULL, so replacement never breaks history.
    await session.execute(delete(PriceRule).where(PriceRule.tenant_id == tenant_id))
    await session.execute(delete(Service).where(Service.tenant_id == tenant_id))
    await session.flush()

    service_ids: dict[str, uuid.UUID] = {}
    for entry in config.services:
        service = Service(
            tenant_id=tenant_id,
            name=entry.name,
            name_normalized=entry.name.strip().lower(),
            description=entry.description,
            duration_minutes=entry.duration_minutes,
            category=entry.category,
            active=entry.active,
        )
        session.add(service)
        await session.flush()
        service_ids[entry.name.strip().lower()] = service.id

    for price in config.prices:
        session.add(
            PriceRule(
                tenant_id=tenant_id,
                service_id=service_ids[price.service_name.strip().lower()],
                label=price.label,
                minimum_amount_cents=price.minimum_amount_cents,
                maximum_amount_cents=price.maximum_amount_cents,
                unit=price.unit,
                customer_visible=price.customer_visible,
                approved=price.approved,
            )
        )

    await session.execute(delete(BusinessHours).where(BusinessHours.tenant_id == tenant_id))
    for day in config.hours:
        session.add(
            BusinessHours(
                tenant_id=tenant_id,
                weekday=day.weekday,
                closed=day.closed,
                opens_at=day.opens_at,
                closes_at=day.closes_at,
            )
        )

    await session.execute(delete(HolidayOverride).where(HolidayOverride.tenant_id == tenant_id))
    for override in config.holiday_overrides:
        session.add(
            HolidayOverride(
                tenant_id=tenant_id,
                date=date.fromisoformat(override.date),
                closed=override.closed,
                opens_at=override.opens_at,
                closes_at=override.closes_at,
                note=override.note,
            )
        )


async def approve_version(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    context: AdminContext,
    notes: str | None = None,
) -> ConfigVersion:
    """Approve the pending version and apply it atomically."""
    pending = await get_open_draft(session, tenant_id)
    if pending is None or pending.state is not ConfigVersionState.PENDING_REVIEW:
        raise ValidationFailedError("No version is pending review.")

    config = _validate_payload(pending.payload)

    previous = await get_active_version(session, tenant_id)
    if previous is not None:
        previous.state = ConfigVersionState.SUPERSEDED
        await session.flush()  # release the one-active unique slot first

    pending.state = ConfigVersionState.ACTIVE
    pending.reviewed_by = context.actor_external_user_id
    pending.reviewed_at = datetime.now(UTC)
    pending.review_notes = notes

    await _apply_to_live_tables(session, tenant_id, config)

    # Approval also marks the safety sign-off used by activation readiness.
    tenant_config = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    ).scalar_one()
    tenant_config.approved_at = datetime.now(UTC)
    tenant_config.approved_by = context.actor_external_user_id

    await write_audit(
        session,
        action="config.approved",
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        tenant_id=tenant_id,
        resource_type="config_version",
        resource_id=str(pending.id),
        before={"active_version": previous.version if previous else None},
        after={"active_version": pending.version},
        request_id=context.request_id,
    )
    logger.info("config_approved", tenant_id=str(tenant_id), version=pending.version)
    return pending


async def reject_version(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    context: AdminContext,
    notes: str,
) -> ConfigVersion:
    pending = await get_open_draft(session, tenant_id)
    if pending is None or pending.state is not ConfigVersionState.PENDING_REVIEW:
        raise ValidationFailedError("No version is pending review.")
    pending.state = ConfigVersionState.REJECTED
    pending.reviewed_by = context.actor_external_user_id
    pending.reviewed_at = datetime.now(UTC)
    pending.review_notes = notes
    await write_audit(
        session,
        action="config.rejected",
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        tenant_id=tenant_id,
        resource_type="config_version",
        resource_id=str(pending.id),
        after={"version": pending.version, "notes": notes},
        request_id=context.request_id,
    )
    return pending


async def rollback_to_version(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    version: int,
    context: AdminContext,
) -> ConfigVersion:
    """Re-apply an older snapshot as a new active version."""
    target = (
        await session.execute(
            select(ConfigVersion).where(
                ConfigVersion.tenant_id == tenant_id, ConfigVersion.version == version
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotFoundError("Version not found.")
    if target.state not in (ConfigVersionState.SUPERSEDED, ConfigVersionState.ACTIVE):
        raise ValidationFailedError("Only previously approved versions can be rolled back to.")
    if target.state is ConfigVersionState.ACTIVE:
        raise ValidationFailedError("That version is already active.")
    if await get_open_draft(session, tenant_id) is not None:
        raise ConflictError("Resolve the open draft before rolling back.")

    config = _validate_payload(target.payload)

    previous = await get_active_version(session, tenant_id)
    if previous is not None:
        previous.state = ConfigVersionState.SUPERSEDED
        await session.flush()

    max_version = (
        await session.execute(
            select(func.coalesce(func.max(ConfigVersion.version), 0)).where(
                ConfigVersion.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    restored = ConfigVersion(
        tenant_id=tenant_id,
        version=max_version + 1,
        state=ConfigVersionState.ACTIVE,
        payload=target.payload,
        created_by=context.actor_external_user_id,
        reviewed_by=context.actor_external_user_id,
        reviewed_at=datetime.now(UTC),
        review_notes=f"Rollback to version {version}",
    )
    session.add(restored)
    await session.flush()

    await _apply_to_live_tables(session, tenant_id, config)

    await write_audit(
        session,
        action="config.rolled_back",
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        tenant_id=tenant_id,
        resource_type="config_version",
        resource_id=str(restored.id),
        before={"active_version": previous.version if previous else None},
        after={"active_version": restored.version, "restored_from": version},
        request_id=context.request_id,
    )
    return restored


async def list_versions(
    session: AsyncSession, *, tenant_id: uuid.UUID, limit: int = 50
) -> list[ConfigVersion]:
    await _get_tenant(session, tenant_id)
    return list(
        (
            await session.execute(
                select(ConfigVersion)
                .where(ConfigVersion.tenant_id == tenant_id)
                .order_by(ConfigVersion.version.desc())
                .limit(limit)
            )
        ).scalars()
    )
