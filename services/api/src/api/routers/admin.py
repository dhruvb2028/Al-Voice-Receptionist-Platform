"""Platform-admin router.

A separate protected route group: every endpoint depends on
``require_platform_admin``, which 404s for non-admin principals so the
admin surface is invisible to client users. Admin endpoints select
tenants explicitly by ID — the only place that is permitted.
"""

import uuid
from typing import Annotated

from ai_database.enums import TenantStatus
from ai_database.repositories import AdminContext, AdminRepository
from ai_providers.auth import AuthenticationProvider, ClerkAuthProvider, NullAuthProvider
from ai_shared.errors import NotFoundError, ValidationFailedError
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import require_platform_admin
from api.db import get_session
from api.routers.tenant import call_filters
from api.schemas.admin_tenants import (
    ActivationReadiness,
    LifecycleActionRequest,
    LifecycleActionResponse,
    TenantCreatedResponse,
    TenantCreateRequest,
    TenantListResponse,
)
from api.services import tenant_admin
from api.services.calls import CallDetail, CallListFilters, CallListPage, call_detail, list_calls
from api.services.health import SystemHealth, system_health
from api.services.metrics import (
    PlatformOverview,
    TenantOverview,
    platform_overview,
    tenant_overview,
)
from api.services.onboarding import (
    OnboardingReport,
    OnboardingState,
    activation_report,
    handover_checklist,
    onboarding_state,
    record_step,
    test_call_report,
    waive_step,
)
from api.settings import get_settings

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminTenantView(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    vertical: str
    timezone: str
    country: str
    expected_monthly_calls: int | None
    external_auth_org_id: str | None


def _repo(
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminRepository:
    return AdminRepository(session, context)


def get_auth_provider() -> AuthenticationProvider:
    settings = get_settings()
    if settings.clerk_secret_key:
        return ClerkAuthProvider(secret_key=settings.clerk_secret_key)
    return NullAuthProvider()


@router.get("/tenants")
async def list_tenants(
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    search: Annotated[str | None, Query(max_length=80)] = None,
    status: Annotated[TenantStatus | None, Query()] = None,
    sort: Annotated[str, Query(pattern="^(name|status|created)$")] = "name",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TenantListResponse:
    items, total = await tenant_admin.list_tenants_with_stats(
        session, search=search, status=status, sort=sort, page=page, page_size=page_size
    )
    return TenantListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/tenants", status_code=201)
async def create_tenant(
    request: TenantCreateRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    auth_provider: Annotated[AuthenticationProvider, Depends(get_auth_provider)],
) -> TenantCreatedResponse:
    tenant, owner_invited = await tenant_admin.create_tenant(
        session, request=request, context=context, auth_provider=auth_provider
    )
    return TenantCreatedResponse(
        id=tenant.id,
        slug=tenant.slug,
        status=tenant.status.value,
        external_auth_org_id=tenant.external_auth_org_id,
        owner_invited=owner_invited,
    )


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
        timezone=tenant.timezone,
        country=tenant.country,
        expected_monthly_calls=tenant.expected_monthly_calls,
        external_auth_org_id=tenant.external_auth_org_id,
    )


@router.get("/tenants/{tenant_id}/activation-readiness")
async def read_activation_readiness(
    tenant_id: uuid.UUID,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ActivationReadiness:
    return await tenant_admin.activation_readiness(session, tenant_id=tenant_id)


def _require_confirmation(request: LifecycleActionRequest) -> None:
    if not request.confirm:
        raise ValidationFailedError("This action requires explicit confirmation.")


@router.post("/tenants/{tenant_id}/activate")
async def activate_tenant(
    tenant_id: uuid.UUID,
    request: LifecycleActionRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LifecycleActionResponse:
    _require_confirmation(request)
    tenant = await tenant_admin.transition_tenant(
        session, tenant_id=tenant_id, target=TenantStatus.ACTIVE, context=context
    )
    return LifecycleActionResponse(id=tenant.id, status=tenant.status.value)


@router.post("/tenants/{tenant_id}/pause")
async def pause_tenant(
    tenant_id: uuid.UUID,
    request: LifecycleActionRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LifecycleActionResponse:
    _require_confirmation(request)
    tenant = await tenant_admin.transition_tenant(
        session, tenant_id=tenant_id, target=TenantStatus.PAUSED, context=context
    )
    return LifecycleActionResponse(id=tenant.id, status=tenant.status.value)


@router.post("/tenants/{tenant_id}/begin-testing")
async def begin_testing(
    tenant_id: uuid.UUID,
    request: LifecycleActionRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LifecycleActionResponse:
    _require_confirmation(request)
    tenant = await tenant_admin.transition_tenant(
        session, tenant_id=tenant_id, target=TenantStatus.TESTING, context=context
    )
    return LifecycleActionResponse(id=tenant.id, status=tenant.status.value)


@router.get("/system-health")
async def read_system_health(
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SystemHealth:
    """Live alert evaluation for the operations console."""
    return await system_health(session, max_concurrent_calls=get_settings().max_concurrent_calls)


@router.get("/overview")
async def read_platform_overview(
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlatformOverview:
    """Fleet-wide health: tenants, live calls, failures, usage, and the
    operational warnings that need someone to act."""
    return await platform_overview(session)


@router.get("/tenants/{tenant_id}/overview")
async def read_tenant_overview(
    tenant_id: uuid.UUID,
    repo: Annotated[AdminRepository, Depends(_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
    window_days: int = Query(default=30, ge=1, le=365),
) -> TenantOverview:
    """One tenant's dashboard metrics, as the client sees them."""
    if await repo.get_tenant(tenant_id) is None:
        raise NotFoundError("Tenant not found.")
    return await tenant_overview(session, tenant_id, window_days=window_days)


# --- Call inspection (admin-expanded view) -----------------------------------


@router.get("/tenants/{tenant_id}/calls")
async def admin_list_calls(
    tenant_id: uuid.UUID,
    repo: Annotated[AdminRepository, Depends(_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
    filters: Annotated[CallListFilters, Depends(call_filters)],
) -> CallListPage:
    """Call history for one tenant, same filters as the client list."""
    if await repo.get_tenant(tenant_id) is None:
        raise NotFoundError("Tenant not found.")
    return await list_calls(session, tenant_id, filters)


@router.get("/tenants/{tenant_id}/calls/{call_id}")
async def admin_read_call(
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    repo: Annotated[AdminRepository, Depends(_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CallDetail:
    """Full call detail with the admin expansion: tool executions with
    redacted payloads, guardrail events, provider IDs, latency stages,
    failure category, and the raw event timeline."""
    if await repo.get_tenant(tenant_id) is None:
        raise NotFoundError("Tenant not found.")
    detail = await call_detail(session, tenant_id, call_id, admin=True)
    if detail is None:
        raise NotFoundError("Call not found.")
    return detail


# --- Onboarding workflow -----------------------------------------------------


class RecordStepRequest(BaseModel):
    passed: bool = True
    note: str | None = Field(default=None, max_length=500)


class WaiveStepRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


@router.get("/tenants/{tenant_id}/onboarding")
async def read_onboarding(
    tenant_id: uuid.UUID,
    repo: Annotated[AdminRepository, Depends(_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OnboardingState:
    """Every onboarding step with its live status and blockers."""
    if await repo.get_tenant(tenant_id) is None:
        raise NotFoundError("Tenant not found.")
    return await onboarding_state(session, tenant_id)


@router.post("/tenants/{tenant_id}/onboarding/{step_key}/record")
async def record_onboarding_step(
    tenant_id: uuid.UUID,
    step_key: str,
    request: RecordStepRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OnboardingState:
    """Attest that a human-judgement step passed. Audited."""
    return await record_step(
        session,
        tenant_id=tenant_id,
        step_key=step_key,
        passed=request.passed,
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        note=request.note,
    )


@router.post("/tenants/{tenant_id}/onboarding/{step_key}/waive")
async def waive_onboarding_step(
    tenant_id: uuid.UUID,
    step_key: str,
    request: WaiveStepRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OnboardingState:
    """Skip a waivable step, recording who decided and why."""
    return await waive_step(
        session,
        tenant_id=tenant_id,
        step_key=step_key,
        reason=request.reason,
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
    )


@router.get("/tenants/{tenant_id}/onboarding/reports/{report}")
async def read_onboarding_report(
    tenant_id: uuid.UUID,
    report: str,
    repo: Annotated[AdminRepository, Depends(_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OnboardingReport:
    """Handover checklist, test-call report, or activation report."""
    if await repo.get_tenant(tenant_id) is None:
        raise NotFoundError("Tenant not found.")
    builders = {
        "handover": handover_checklist,
        "test-calls": test_call_report,
        "activation": activation_report,
    }
    builder = builders.get(report)
    if builder is None:
        raise NotFoundError("Unknown report.")
    return await builder(session, tenant_id)
