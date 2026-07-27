"""Principal model: who is making this request, in which role, for
which tenant. Built only by the auth dependencies after signature and
membership verification — handlers never construct one."""

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class PrincipalRole(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    CLIENT_OWNER = "client_owner"
    CLIENT_STAFF = "client_staff"


class Principal(BaseModel):
    """Verified request identity.

    ``tenant_id`` is present for client roles and absent for the platform
    admin (who selects tenants explicitly on admin routes).
    """

    model_config = ConfigDict(frozen=True)

    external_user_id: str
    role: PrincipalRole
    tenant_id: uuid.UUID | None = None
    clerk_org_id: str | None = None

    @property
    def is_platform_admin(self) -> bool:
        return self.role is PrincipalRole.PLATFORM_ADMIN


class ClerkClaims(BaseModel):
    """Claims extracted from a verified Clerk session token."""

    sub: str
    org_id: str | None = None
    org_role: str | None = None
    platform_role: str | None = None
