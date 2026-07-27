"""Admin tenant-management schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,78}$"
E164_PATTERN = r"^\+[1-9][0-9]{6,14}$"


class TenantCreateRequest(BaseModel):
    business_name: str = Field(min_length=2, max_length=200)
    slug: str = Field(pattern=SLUG_PATTERN)
    timezone: str = Field(min_length=1, max_length=64)
    vertical: str = Field(pattern=r"^(plumbing|hvac|electrical)$")
    primary_owner_email: EmailStr
    primary_phone: str = Field(pattern=E164_PATTERN)
    escalation_number: str = Field(pattern=E164_PATTERN)
    country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    expected_monthly_calls: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        from zoneinfo import available_timezones

        if value not in available_timezones():
            raise ValueError("unknown IANA timezone")
        return value


class TenantCreatedResponse(BaseModel):
    id: uuid.UUID
    slug: str
    status: str
    external_auth_org_id: str | None
    owner_invited: bool


class TenantListItem(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    vertical: str
    status: str
    assigned_numbers: int
    calls_today: int
    calls_this_month: int
    failed_calls_this_month: int
    calendar_health: str
    last_successful_call_at: datetime | None
    usage_minutes_this_month: int
    configuration_ready: bool


class TenantListResponse(BaseModel):
    items: list[TenantListItem]
    total: int
    page: int
    page_size: int


class ActivationBlocker(BaseModel):
    code: str
    message: str
    waivable: bool = False


class ActivationReadiness(BaseModel):
    tenant_id: uuid.UUID
    ready: bool
    blockers: list[ActivationBlocker]


class LifecycleActionRequest(BaseModel):
    # Explicit confirmation — the dashboard shows a confirm dialog and
    # sends true; API refuses without it.
    confirm: bool


class LifecycleActionResponse(BaseModel):
    id: uuid.UUID
    status: str
