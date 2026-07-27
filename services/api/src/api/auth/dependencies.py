"""FastAPI authentication/authorization dependencies.

Chain: bearer token → signature verification → claims → principal
(platform admin via platform_role claim or configured allowlist; client
roles via Clerk org → tenant mapping + active membership) → role guards.

Tenant scope for client endpoints comes ONLY from the verified org
claim. Admin endpoints select tenants explicitly and live under the
separate ``/admin`` router with its own guard.
"""

from typing import Annotated

import structlog
from ai_database.enums import MemberStatus, TenantStatus
from ai_database.models import Tenant, TenantMember
from ai_database.repositories import AdminContext, TenantScopedRepository
from ai_shared.errors import ForbiddenError, UnauthorizedError
from ai_shared.request_id import get_request_id
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import ClerkClaims, Principal, PrincipalRole
from api.auth.verify import JwksCache, TokenVerifier
from api.db import get_session
from api.settings import get_settings

logger = structlog.get_logger()

_verifier: TokenVerifier | None = None

# Client dashboard access is blocked for these tenant states. Paused
# tenants keep dashboard access (calls stop; history remains readable).
_BLOCKED_TENANT_STATUSES = frozenset({TenantStatus.SUSPENDED, TenantStatus.CHURNED})


def get_verifier() -> TokenVerifier:
    global _verifier
    if _verifier is None:
        settings = get_settings()
        if not settings.clerk_jwt_issuer:
            raise UnauthorizedError("Authentication is not configured.")
        jwks_url = (
            settings.clerk_jwks_url
            or f"{settings.clerk_jwt_issuer.rstrip('/')}/.well-known/jwks.json"
        )
        _verifier = TokenVerifier(
            jwks=JwksCache(jwks_url),
            issuer=settings.clerk_jwt_issuer,
            audience=settings.clerk_jwt_audience,
        )
    return _verifier


def reset_verifier() -> None:
    """Test hook."""
    global _verifier
    _verifier = None


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError("Missing bearer token.")
    return token


async def get_claims(request: Request) -> ClerkClaims:
    token = _bearer_token(request)
    return await get_verifier().verify(token)


async def get_principal(
    claims: Annotated[ClerkClaims, Depends(get_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Principal:
    settings = get_settings()

    # Platform admin: claim-based, with a configured allowlist fallback.
    is_admin = claims.platform_role == "platform_admin" or (
        claims.sub in settings.platform_admin_user_id_list
    )
    if is_admin:
        return Principal(external_user_id=claims.sub, role=PrincipalRole.PLATFORM_ADMIN)

    if not claims.org_id:
        raise ForbiddenError("No organization membership in session.")

    tenant = (
        await session.execute(select(Tenant).where(Tenant.external_auth_org_id == claims.org_id))
    ).scalar_one_or_none()
    if tenant is None:
        logger.warning("auth_unknown_org", request_id=get_request_id())
        raise ForbiddenError("Organization is not linked to a tenant.")

    membership = (
        await session.execute(
            select(TenantMember).where(
                TenantMember.tenant_id == tenant.id,
                TenantMember.external_user_id == claims.sub,
            )
        )
    ).scalar_one_or_none()
    if membership is None or membership.status is not MemberStatus.ACTIVE:
        raise ForbiddenError("Membership is not active.")

    if tenant.status in _BLOCKED_TENANT_STATUSES:
        raise ForbiddenError("This account is currently unavailable.")

    role = (
        PrincipalRole.CLIENT_OWNER
        if membership.role.value == "client_owner"
        else PrincipalRole.CLIENT_STAFF
    )
    return Principal(
        external_user_id=claims.sub,
        role=role,
        tenant_id=tenant.id,
        clerk_org_id=claims.org_id,
    )


async def require_platform_admin(
    principal: Annotated[Principal, Depends(get_principal)],
) -> AdminContext:
    if not principal.is_platform_admin:
        # Admin surface does not exist for client users.
        from ai_shared.errors import NotFoundError

        raise NotFoundError("Not found.")
    return AdminContext(
        actor_external_user_id=principal.external_user_id,
        request_id=get_request_id(),
    )


async def require_client(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    if principal.tenant_id is None:
        raise ForbiddenError("A tenant-scoped session is required.")
    return principal


async def require_client_owner(
    principal: Annotated[Principal, Depends(require_client)],
) -> Principal:
    if principal.role is not PrincipalRole.CLIENT_OWNER:
        raise ForbiddenError("Owner access required.")
    return principal


async def get_tenant_repository(
    principal: Annotated[Principal, Depends(require_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantScopedRepository:
    assert principal.tenant_id is not None  # guaranteed by require_client
    repo = TenantScopedRepository(session, principal.tenant_id)
    await repo.bind_rls()
    return repo
