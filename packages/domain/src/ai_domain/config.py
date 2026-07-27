"""Canonical receptionist configuration schema.

The single source of truth for what a tenant's receptionist may know and
do. The API validates drafts against it, the approval workflow snapshots
it, and the voice service loads only an **approved, active** instance of
it — never a draft.

Money is integer cents. Phone numbers are E.164. Every list the model
can speak from (services, prices, areas) lives here — the LLM is never
given latitude beyond this document.
"""

from datetime import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

E164 = Annotated[str, Field(pattern=r"^\+[1-9][0-9]{6,14}$")]


class BusinessIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_name: str = Field(min_length=2, max_length=200)
    timezone: str = Field(min_length=1, max_length=64)
    address: str | None = Field(default=None, max_length=400)
    service_region_label: str | None = Field(default=None, max_length=200)
    business_phone: E164 | None = None
    website: str | None = Field(default=None, max_length=200)
    emergency_contact: E164 | None = None

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        from zoneinfo import available_timezones

        if value not in available_timezones():
            raise ValueError("unknown IANA timezone")
        return value


class GreetingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    greeting: str = Field(min_length=10, max_length=600)
    recording_notice: str | None = Field(default=None, max_length=400)
    after_hours_greeting: str | None = Field(default=None, max_length=600)
    tenant_approved: bool = False


class ServiceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    duration_minutes: int = Field(ge=15, le=480)
    category: str | None = Field(default=None, max_length=80)
    active: bool = True


class PriceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(min_length=2, max_length=160)
    label: str = Field(min_length=2, max_length=160)
    minimum_amount_cents: int | None = Field(default=None, ge=0)
    maximum_amount_cents: int | None = Field(default=None, ge=0)
    unit: Literal["flat", "range", "hourly", "per_visit"] = "flat"
    customer_visible: bool = True
    approved: bool = False

    @model_validator(mode="after")
    def _range_valid(self) -> "PriceEntry":
        if (
            self.minimum_amount_cents is not None
            and self.maximum_amount_cents is not None
            and self.minimum_amount_cents > self.maximum_amount_cents
        ):
            raise ValueError("minimum price exceeds maximum")
        if self.minimum_amount_cents is None and self.maximum_amount_cents is None:
            raise ValueError("a price requires at least one amount")
        return self


class DayHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)  # 0 = Monday
    closed: bool = False
    opens_at: time | None = None
    closes_at: time | None = None

    @model_validator(mode="after")
    def _hours_valid(self) -> "DayHours":
        if self.closed:
            return self
        if self.opens_at is None or self.closes_at is None:
            raise ValueError("open days require opening and closing times")
        if self.opens_at >= self.closes_at:
            raise ValueError("opening time must be before closing time")
        return self


class HolidayOverrideEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    closed: bool = True
    opens_at: time | None = None
    closes_at: time | None = None
    note: str | None = Field(default=None, max_length=200)


class ServiceAreaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    postal_codes: list[str] = Field(default_factory=list, max_length=500)
    cities: list[str] = Field(default_factory=list, max_length=100)
    radius_miles: int | None = Field(default=None, ge=1, le=200)
    exclusions: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("postal_codes")
    @classmethod
    def _zip_shape(cls, values: list[str]) -> list[str]:
        for zip_code in values:
            if not zip_code.strip():
                raise ValueError("empty postal code")
        return [z.strip() for z in values]

    @model_validator(mode="after")
    def _not_empty(self) -> "ServiceAreaConfig":
        if not self.postal_codes and not self.cities and self.radius_miles is None:
            raise ValueError("service area must define postal codes, cities, or a radius")
        return self


class EscalationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emergency_destination: E164
    human_request_behavior: Literal["transfer", "message"] = "transfer"
    after_hours_destination: E164 | None = None
    failed_intent_threshold: int = Field(default=2, ge=1, le=5)
    frustration_threshold: int = Field(default=2, ge=1, le=5)
    transfer_timeout_seconds: int = Field(default=25, ge=10, le=120)
    message_fallback: bool = True  # always take a message when transfer fails

    @field_validator("message_fallback")
    @classmethod
    def _fallback_mandatory(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("message fallback cannot be disabled")
        return value


class VoiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: str = Field(min_length=1, max_length=120)
    speaking_style: str | None = Field(default=None, max_length=80)
    language: Literal["en"] = "en"
    filler_phrases: list[str] = Field(default_factory=list, max_length=20)
    max_call_seconds: int = Field(default=900, ge=60, le=3600)


class ReceptionistConfig(BaseModel):
    """Complete tenant configuration draft/snapshot."""

    model_config = ConfigDict(extra="forbid")

    identity: BusinessIdentity
    greeting: GreetingConfig
    services: list[ServiceEntry] = Field(min_length=1, max_length=100)
    prices: list[PriceEntry] = Field(default_factory=list, max_length=200)
    hours: list[DayHours] = Field(min_length=7, max_length=7)
    holiday_overrides: list[HolidayOverrideEntry] = Field(default_factory=list, max_length=50)
    service_area: ServiceAreaConfig
    escalation: EscalationPolicy
    voice: VoiceSettings

    @model_validator(mode="after")
    def _cross_checks(self) -> "ReceptionistConfig":
        weekdays = sorted(day.weekday for day in self.hours)
        if weekdays != list(range(7)):
            raise ValueError("hours must cover each weekday exactly once")

        service_names = {s.name.strip().lower() for s in self.services}
        if len(service_names) != len(self.services):
            raise ValueError("service names must be unique")

        for price in self.prices:
            if price.service_name.strip().lower() not in service_names:
                raise ValueError(
                    f"price '{price.label}' references unknown service '{price.service_name}'"
                )
        return self
