"""Tenant administration: creation, activation readiness, lifecycle.

Every mutation writes an audit event. Nothing here activates a tenant
implicitly — activation is an explicit, confirmed, fully-blocked-until-
ready action.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from ai_database.audit import write_audit
from ai_database.enums import (
    CalendarConnectionStatus,
    CallOutcome,
    MemberRole,
    MemberStatus,
    TenantStatus,
)
from ai_database.models import (
    BusinessHours,
    CalendarConnection,
    Call,
    PhoneNumber,
    Service,
    Tenant,
    TenantConfig,
    TenantMember,
    UsageRecord,
)
from ai_database.repositories import AdminContext
from ai_providers.auth import AuthenticationProvider, ProviderError
from ai_shared.errors import ConflictError, NotFoundError, ValidationFailedError
from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.admin_tenants import (
    ActivationBlocker,
    ActivationReadiness,
    TenantCreateRequest,
    TenantListItem,
)

logger = structlog.get_logger()

# Lifecycle transitions the admin API permits.
_ALLOWED_TRANSITIONS: dict[TenantStatus, frozenset[TenantStatus]] = {
    TenantStatus.ONBOARDING: frozenset({TenantStatus.TESTING}),
    TenantStatus.TESTING: frozenset({TenantStatus.ACTIVE}),
    TenantStatus.ACTIVE: frozenset({TenantStatus.PAUSED, TenantStatus.SUSPENDED}),
    TenantStatus.PAUSED: frozenset({TenantStatus.ACTIVE, TenantStatus.CHURNED}),
    TenantStatus.SUSPENDED: frozenset({TenantStatus.ACTIVE, TenantStatus.CHURNED}),
}


async def create_tenant(
    session: AsyncSession,
    *,
    request: TenantCreateRequest,
    context: AdminContext,
    auth_provider: AuthenticationProvider,
) -> tuple[Tenant, bool]:
    """Create tenant + config + auth org + owner invitation + audit.

    Returns (tenant, owner_invited). Never activates.
    """
    existing = (
        await session.execute(select(Tenant).where(Tenant.slug == request.slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"Slug '{request.slug}' is already in use.")

    org = await auth_provider.create_organization(name=request.business_name, slug=request.slug)

    tenant = Tenant(
        name=request.business_name,
        slug=request.slug,
        vertical=request.vertical,
        timezone=request.timezone,
        status=TenantStatus.ONBOARDING,
        external_auth_org_id=org.external_org_id,
        country=request.country,
        expected_monthly_calls=request.expected_monthly_calls,
    )
    session.add(tenant)
    await session.flush()

    session.add(
        TenantConfig(
            tenant_id=tenant.id,
            timezone=request.timezone,
            escalation_number=request.escalation_number,
            business_phone=request.primary_phone,
        )
    )

    owner_invited = True
    try:
        await auth_provider.invite_owner(
            external_org_id=org.external_org_id, email=request.primary_owner_email
        )
    except ProviderError:
        # Creation still succeeds; the invitation is retried from the
        # dashboard. The audit row records the failure.
        owner_invited = False

    await write_audit(
        session,
        action="tenant.created",
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        tenant_id=tenant.id,
        resource_type="tenant",
        resource_id=str(tenant.id),
        after={
            "name": request.business_name,
            "slug": request.slug,
            "vertical": request.vertical,
            "timezone": request.timezone,
            "country": request.country,
            "owner_invited": owner_invited,
        },
        request_id=context.request_id,
    )
    logger.info("tenant_created", tenant_id=str(tenant.id), slug=request.slug)
    return tenant, owner_invited


async def activation_readiness(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> ActivationReadiness:
    """Machine-readable activation blockers."""
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Tenant not found.")

    config = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    ).scalar_one_or_none()
    state: dict[str, Any] = (config.activation_state or {}) if config else {}

    blockers: list[ActivationBlocker] = []

    def blocked(code: str, message: str, *, waivable: bool = False) -> None:
        blockers.append(ActivationBlocker(code=code, message=message, waivable=waivable))

    if config is None or not (config.greeting or "").strip():
        blocked("greeting_missing", "A greeting must be configured and approved.")
    elif not state.get("greeting_approved"):
        blocked("greeting_unapproved", "The greeting text has not been approved.")

    if (
        config is not None
        and config.recording_enabled
        and not ((config.recording_consent_text or "").strip())
    ):
        blocked(
            "recording_notice_missing",
            "Recording is enabled but no consent announcement is configured.",
        )

    service_count = (
        await session.execute(
            select(func.count())
            .select_from(Service)
            .where(Service.tenant_id == tenant_id, Service.active.is_(True))
        )
    ).scalar_one()
    if service_count == 0:
        blocked("no_services", "At least one active service is required.")

    hours_count = (
        await session.execute(
            select(func.count())
            .select_from(BusinessHours)
            .where(BusinessHours.tenant_id == tenant_id)
        )
    ).scalar_one()
    if hours_count == 0:
        blocked("no_business_hours", "Business hours must be configured.")

    if config is None or not config.escalation_number:
        blocked("escalation_number_missing", "An escalation number is required.")
    elif not state.get("escalation_verified"):
        blocked(
            "escalation_unverified",
            "The escalation number has not been verified with a test dial.",
        )

    number_count = (
        await session.execute(
            select(func.count())
            .select_from(PhoneNumber)
            .where(PhoneNumber.tenant_id == tenant_id, PhoneNumber.active.is_(True))
        )
    ).scalar_one()
    if number_count == 0:
        blocked("no_phone_number", "A phone number must be assigned.")

    calendar = (
        await session.execute(
            select(CalendarConnection).where(CalendarConnection.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if calendar is None or calendar.status is not CalendarConnectionStatus.CONNECTED:
        blocked("calendar_unhealthy", "A healthy calendar connection is required.")

    if not state.get("browser_test_passed"):
        blocked("browser_test_missing", "A browser test call has not passed.")

    if not state.get("phone_test_passed") and not state.get("phone_test_waived"):
        blocked(
            "phone_test_missing",
            "A real phone test call has not passed (waivable with justification).",
            waivable=True,
        )

    if config is None or config.approved_at is None:
        blocked("safety_config_unapproved", "Safety configuration has not been approved.")

    return ActivationReadiness(tenant_id=tenant_id, ready=not blockers, blockers=blockers)


async def transition_tenant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    target: TenantStatus,
    context: AdminContext,
) -> Tenant:
    """Confirmed lifecycle transition with audit; activation re-checks
    readiness server-side regardless of what the dashboard claimed."""
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Tenant not found.")

    allowed = _ALLOWED_TRANSITIONS.get(tenant.status, frozenset())
    if target not in allowed:
        raise ValidationFailedError(
            f"Cannot move tenant from '{tenant.status.value}' to '{target.value}'."
        )

    if target is TenantStatus.ACTIVE and tenant.status is not TenantStatus.PAUSED:
        readiness = await activation_readiness(session, tenant_id=tenant_id)
        if not readiness.ready:
            raise ValidationFailedError(
                "Tenant is not ready for activation: "
                + ", ".join(b.code for b in readiness.blockers)
            )

    before_status = tenant.status.value
    tenant.status = target
    if target is TenantStatus.ACTIVE and tenant.activated_at is None:
        tenant.activated_at = datetime.now(UTC)

    await write_audit(
        session,
        action=f"tenant.{target.value}",
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        tenant_id=tenant.id,
        resource_type="tenant",
        resource_id=str(tenant.id),
        before={"status": before_status},
        after={"status": target.value},
        request_id=context.request_id,
    )
    logger.info(
        "tenant_transitioned",
        tenant_id=str(tenant.id),
        from_status=before_status,
        to_status=target.value,
    )
    return tenant


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def list_tenants_with_stats(
    session: AsyncSession,
    *,
    search: str | None = None,
    status: TenantStatus | None = None,
    sort: str = "name",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[TenantListItem], int]:
    now = datetime.now(UTC)
    day0, month0 = _day_start(now), _month_start(now)

    stmt: Select[tuple[Tenant]] = select(Tenant).where(Tenant.archived_at.is_(None))
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(Tenant.name).like(pattern) | func.lower(Tenant.slug).like(pattern)
        )
    if status is not None:
        stmt = stmt.where(Tenant.status == status)

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()

    order_column = {
        "name": Tenant.name,
        "status": Tenant.status,
        "created": Tenant.created_at,
    }.get(sort, Tenant.name)
    stmt = stmt.order_by(order_column).limit(page_size).offset((page - 1) * page_size)
    tenants = list((await session.execute(stmt)).scalars())
    if not tenants:
        return [], total

    ids = [t.id for t in tenants]

    number_counts: dict[uuid.UUID, int] = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(PhoneNumber.tenant_id, func.count())
                .where(PhoneNumber.tenant_id.in_(ids), PhoneNumber.active.is_(True))
                .group_by(PhoneNumber.tenant_id)
            )
        ).all()
    }
    call_stats = {
        row[0]: row
        for row in (
            await session.execute(
                select(
                    Call.tenant_id,
                    func.count().filter(Call.started_at >= day0),
                    func.count().filter(Call.started_at >= month0),
                    func.count().filter(
                        Call.started_at >= month0, Call.outcome == CallOutcome.FAILED
                    ),
                    func.max(
                        case(
                            (
                                Call.outcome.in_(
                                    [
                                        CallOutcome.BOOKED,
                                        CallOutcome.MESSAGE_TAKEN,
                                        CallOutcome.TRANSFERRED,
                                        CallOutcome.ANSWERED_INQUIRY,
                                    ]
                                ),
                                Call.started_at,
                            ),
                        )
                    ),
                )
                .where(Call.tenant_id.in_(ids))
                .group_by(Call.tenant_id)
            )
        ).all()
    }
    usage_minutes: dict[uuid.UUID, int] = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(
                    UsageRecord.tenant_id,
                    func.coalesce(func.sum(UsageRecord.quantity), 0),
                )
                .where(
                    UsageRecord.tenant_id.in_(ids),
                    UsageRecord.usage_type == "call_minutes",
                    UsageRecord.recorded_at >= month0,
                )
                .group_by(UsageRecord.tenant_id)
            )
        ).all()
    }
    calendars = {
        c.tenant_id: c.status
        for c in (
            await session.execute(
                select(CalendarConnection).where(CalendarConnection.tenant_id.in_(ids))
            )
        ).scalars()
    }

    items: list[TenantListItem] = []
    for tenant in tenants:
        stats = call_stats.get(tenant.id)
        readiness = await activation_readiness(session, tenant_id=tenant.id)
        calendar_status = calendars.get(tenant.id)
        items.append(
            TenantListItem(
                id=tenant.id,
                name=tenant.name,
                slug=tenant.slug,
                vertical=tenant.vertical,
                status=tenant.status.value,
                assigned_numbers=number_counts.get(tenant.id, 0),
                calls_today=stats[1] if stats else 0,
                calls_this_month=stats[2] if stats else 0,
                failed_calls_this_month=stats[3] if stats else 0,
                calendar_health=calendar_status.value if calendar_status else "not_connected",
                last_successful_call_at=stats[4] if stats else None,
                usage_minutes_this_month=int(usage_minutes.get(tenant.id, 0)),
                configuration_ready=readiness.ready,
            )
        )
    return items, total


async def ensure_member_invited(
    session: AsyncSession, *, tenant_id: uuid.UUID, email_user_id: str
) -> None:
    """Record the invited owner as a pending membership placeholder."""
    session.add(
        TenantMember(
            tenant_id=tenant_id,
            external_user_id=email_user_id,
            role=MemberRole.CLIENT_OWNER,
            status=MemberStatus.INVITED,
        )
    )
