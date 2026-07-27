"""Complete initial schema.

Conventions:
- UUID primary keys generated client-side (uuid4) so rows are addressable
  before flush.
- All timestamps are timezone-aware UTC (``TIMESTAMPTZ``).
- Every tenant-owned table carries ``tenant_id`` with an index; composite
  indexes cover the dominant dashboard queries.
- Cascades: configuration children cascade with their tenant; call, usage,
  and audit history must never be deleted by accident — their tenant FK is
  RESTRICT, and removal is an explicit, audited archive procedure.
- Sensitive values are stored via the encryption service
  (``ai_shared.crypto``); ``*_hash`` columns hold keyed lookup hashes,
  ``*_last_four`` display fragments.
"""

import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_database.enums import (
    BookingStatus,
    CalendarConnectionStatus,
    CallDirection,
    CallOutcome,
    CallTransport,
    ConfigVersionState,
    DeliveryStatus,
    EscalationReason,
    EscalationStatus,
    GuardrailAction,
    GuardrailType,
    MemberRole,
    MemberStatus,
    ProcessingStatus,
    ProviderEventStatus,
    ReconciliationStatus,
    RecordingStatus,
    TenantStatus,
    ToolExecutionStatus,
    TranscriptStatus,
    TurnRole,
    Urgency,
)
from ai_database.metadata import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(UTC)


def _enum(enum_cls: type, name: str) -> Enum:
    """Native PG enum storing the string values (not member names)."""
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda cls: [member.value for member in cls],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=sa_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=sa_text("now()"),
    )


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    vertical: Mapped[str] = mapped_column(String(40), nullable=False, default="plumbing")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/New_York")
    status: Mapped[TenantStatus] = mapped_column(
        _enum(TenantStatus, "tenant_status"), nullable=False, default=TenantStatus.ONBOARDING
    )
    plan_label: Mapped[str | None] = mapped_column(String(80))
    external_auth_org_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")
    expected_monthly_calls: Mapped[int | None] = mapped_column(Integer)
    # Soft archive: churned tenants keep history until contractual purge.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,78}$'", name="slug_format"),)


class TenantMember(TimestampMixin, Base):
    __tablename__ = "tenant_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    external_user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[MemberRole] = mapped_column(_enum(MemberRole, "member_role"), nullable=False)
    status: Mapped[MemberStatus] = mapped_column(
        _enum(MemberStatus, "member_status"), nullable=False, default=MemberStatus.INVITED
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "external_user_id", name="uq_member_tenant_user"),
        Index("ix_tenant_members_tenant_id", "tenant_id"),
    )


class TenantConfig(TimestampMixin, Base):
    __tablename__ = "tenant_config"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    greeting: Mapped[str | None] = mapped_column(Text)
    persona: Mapped[str | None] = mapped_column(Text)
    voice_id: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    recording_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recording_consent_text: Mapped[str | None] = mapped_column(Text)
    #: days recordings are kept (None -> platform default of 30; max 90)
    recording_retention_days: Mapped[int | None] = mapped_column(Integer)
    escalation_number: Mapped[str | None] = mapped_column(String(20))
    max_call_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    timezone: Mapped[str | None] = mapped_column(String(64))
    # Flexible by nature: list of ZIP codes / radius spec varies per tenant.
    service_area: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Structured escalation rules (triggers, windows, targets) evolve
    # faster than the schema; validated by Pydantic before write.
    escalation_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    business_phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(200))
    after_hours_greeting: Mapped[str | None] = mapped_column(Text)
    speaking_style: Mapped[str | None] = mapped_column(String(80))
    filler_phrases: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Activation checklist flags set by admin workflows (browser/phone
    # test results, waivers, escalation verification). Flexible by
    # design: checks evolve without schema churn.
    activation_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        CheckConstraint(
            "max_call_seconds > 0 AND max_call_seconds <= 3600", name="max_call_seconds_bounds"
        ),
        CheckConstraint(
            "escalation_number IS NULL OR escalation_number ~ '^\\+[1-9][0-9]{6,14}$'",
            name="escalation_number_e164",
        ),
    )


class Service(TimestampMixin, Base):
    __tablename__ = "services"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # Normalized (lowercased, trimmed) for the uniqueness constraint.
    name_normalized: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    category: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        UniqueConstraint("tenant_id", "name_normalized", name="uq_service_tenant_name"),
        CheckConstraint("duration_minutes > 0 AND duration_minutes <= 480", name="duration_bounds"),
        Index("ix_services_tenant_id", "tenant_id"),
    )


class PriceRule(TimestampMixin, Base):
    __tablename__ = "price_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    # Money is integer cents, never floating point.
    minimum_amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    maximum_amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    unit: Mapped[str] = mapped_column(String(40), nullable=False, default="flat")
    customer_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint(
            "(minimum_amount_cents IS NULL OR minimum_amount_cents >= 0) AND "
            "(maximum_amount_cents IS NULL OR maximum_amount_cents >= 0)",
            name="amounts_non_negative",
        ),
        CheckConstraint(
            "minimum_amount_cents IS NULL OR maximum_amount_cents IS NULL "
            "OR minimum_amount_cents <= maximum_amount_cents",
            name="min_lte_max",
        ),
        Index("ix_price_rules_tenant_id", "tenant_id"),
        Index("ix_price_rules_service_id", "service_id"),
    )


class BusinessHours(TimestampMixin, Base):
    __tablename__ = "business_hours"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Monday .. 6=Sunday
    opens_at: Mapped[time | None] = mapped_column(Time)
    closes_at: Mapped[time | None] = mapped_column(Time)
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "weekday", name="uq_hours_tenant_weekday"),
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="weekday_range"),
        CheckConstraint(
            "closed OR (opens_at IS NOT NULL AND closes_at IS NOT NULL AND opens_at < closes_at)",
            name="open_hours_valid",
        ),
        Index("ix_business_hours_tenant_id", "tenant_id"),
    )


class HolidayOverride(Base):
    __tablename__ = "holiday_overrides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    opens_at: Mapped[time | None] = mapped_column(Time)
    closes_at: Mapped[time | None] = mapped_column(Time)
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("tenant_id", "date", name="uq_holiday_tenant_date"),
        CheckConstraint(
            "closed OR (opens_at IS NOT NULL AND closes_at IS NOT NULL AND opens_at < closes_at)",
            name="override_hours_valid",
        ),
        Index("ix_holiday_overrides_tenant_id", "tenant_id"),
    )


class PhoneNumber(TimestampMixin, Base):
    __tablename__ = "phone_numbers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    e164: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="twilio")
    provider_sid: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    voice_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("e164 ~ '^\\+[1-9][0-9]{6,14}$'", name="e164_format"),
        Index("ix_phone_numbers_tenant_id", "tenant_id"),
    )


class Call(TimestampMixin, Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # RESTRICT: call history is never deleted by a tenant cascade.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    phone_number_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("phone_numbers.id", ondelete="SET NULL")
    )
    provider_call_sid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    from_number_encrypted: Mapped[str | None] = mapped_column(Text)
    from_number_hash: Mapped[str | None] = mapped_column(String(64))
    from_number_last_four: Mapped[str | None] = mapped_column(String(4))
    to_number: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[CallDirection] = mapped_column(
        _enum(CallDirection, "call_direction"), nullable=False, default=CallDirection.INBOUND
    )
    transport: Mapped[CallTransport] = mapped_column(
        _enum(CallTransport, "call_transport"), nullable=False, default=CallTransport.PHONE
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[CallOutcome | None] = mapped_column(_enum(CallOutcome, "call_outcome"))
    urgency: Mapped[Urgency | None] = mapped_column(_enum(Urgency, "urgency"))
    recording_status: Mapped[RecordingStatus] = mapped_column(
        _enum(RecordingStatus, "recording_status"),
        nullable=False,
        default=RecordingStatus.DISABLED,
    )
    recording_object_key: Mapped[str | None] = mapped_column(String(300))
    # Legal hold exempts a recording from retention deletion.
    recording_legal_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transcript_status: Mapped[TranscriptStatus] = mapped_column(
        _enum(TranscriptStatus, "transcript_status"),
        nullable=False,
        default=TranscriptStatus.PENDING,
    )
    post_processing_status: Mapped[ProcessingStatus] = mapped_column(
        _enum(ProcessingStatus, "processing_status"),
        nullable=False,
        default=ProcessingStatus.PENDING,
    )
    estimated_cost_cents: Mapped[int | None] = mapped_column(BigInteger)
    failure_category: Mapped[str | None] = mapped_column(String(80))
    # Safe = pre-redacted, no caller PII; shown in the admin failure queue.
    failure_detail_safe: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="duration_non_negative"
        ),
        Index("ix_calls_tenant_started", "tenant_id", "started_at"),
        Index("ix_calls_tenant_outcome", "tenant_id", "outcome"),
        Index("ix_calls_from_hash", "tenant_id", "from_number_hash"),
        Index("ix_calls_post_processing", "post_processing_status"),
    )


class Turn(Base):
    __tablename__ = "turns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[TurnRole] = mapped_column(_enum(TurnRole, "turn_role"), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endpointing_ms: Mapped[int | None] = mapped_column(Integer)
    stt_finalization_ms: Mapped[int | None] = mapped_column(Integer)
    llm_ttft_ms: Mapped[int | None] = mapped_column(Integer)
    tts_ttfb_ms: Mapped[int | None] = mapped_column(Integer)
    first_playback_ms: Mapped[int | None] = mapped_column(Integer)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    barge_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    interrupted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=sa_text("now()")
    )

    __table_args__ = (
        UniqueConstraint("call_id", "turn_index", name="uq_turn_call_index"),
        CheckConstraint("turn_index >= 0", name="turn_index_non_negative"),
        Index("ix_turns_tenant_id", "tenant_id"),
        Index("ix_turns_call_id", "call_id"),
    )


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    input_redacted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_redacted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[ToolExecutionStatus] = mapped_column(
        _enum(ToolExecutionStatus, "tool_execution_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_category: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        Index("ix_tool_executions_tenant_id", "tenant_id"),
        Index("ix_tool_executions_call_id", "call_id"),
    )


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL")
    )
    customer_name: Mapped[str | None] = mapped_column(String(200))
    customer_phone_encrypted: Mapped[str | None] = mapped_column(Text)
    customer_phone_hash: Mapped[str | None] = mapped_column(String(64))
    customer_phone_last_four: Mapped[str | None] = mapped_column(String(4))
    address_encrypted: Mapped[str | None] = mapped_column(Text)
    notes_encrypted: Mapped[str | None] = mapped_column(Text)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    external_calendar_event_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    status: Mapped[BookingStatus] = mapped_column(
        _enum(BookingStatus, "booking_status"), nullable=False, default=BookingStatus.PENDING
    )
    reconciliation_status: Mapped[ReconciliationStatus] = mapped_column(
        _enum(ReconciliationStatus, "reconciliation_status"),
        nullable=False,
        default=ReconciliationStatus.NOT_REQUIRED,
    )

    __table_args__ = (
        Index("ix_bookings_tenant_scheduled", "tenant_id", "scheduled_at"),
        Index("ix_bookings_tenant_status", "tenant_id", "status"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))
    customer_name: Mapped[str | None] = mapped_column(String(200))
    customer_phone_encrypted: Mapped[str | None] = mapped_column(Text)
    customer_phone_last_four: Mapped[str | None] = mapped_column(String(4))
    body_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    urgency: Mapped[Urgency] = mapped_column(
        _enum(Urgency, "urgency"), nullable=False, default=Urgency.ROUTINE
    )
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        _enum(DeliveryStatus, "delivery_status"), nullable=False, default=DeliveryStatus.PENDING
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=sa_text("now()")
    )

    __table_args__ = (
        Index("ix_messages_tenant_created", "tenant_id", "created_at"),
        Index("ix_messages_tenant_urgency", "tenant_id", "urgency"),
    )


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[EscalationReason] = mapped_column(
        _enum(EscalationReason, "escalation_reason"), nullable=False
    )
    destination_last_four: Mapped[str | None] = mapped_column(String(4))
    initiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[EscalationStatus] = mapped_column(
        _enum(EscalationStatus, "escalation_status"),
        nullable=False,
        default=EscalationStatus.INITIATED,
    )
    fallback_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("ix_escalations_tenant_id", "tenant_id"),
        Index("ix_escalations_call_id", "call_id"),
    )


class GuardrailEvent(Base):
    __tablename__ = "guardrail_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    turn_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    guardrail_type: Mapped[GuardrailType] = mapped_column(
        _enum(GuardrailType, "guardrail_type"), nullable=False
    )
    input_redacted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    action: Mapped[GuardrailAction] = mapped_column(
        _enum(GuardrailAction, "guardrail_action"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=sa_text("now()")
    )

    __table_args__ = (Index("ix_guardrail_events_tenant_created", "tenant_id", "created_at"),)


class CalendarConnection(TimestampMixin, Base):
    __tablename__ = "calendar_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="google")
    external_calendar_id: Mapped[str | None] = mapped_column(String(200))
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CalendarConnectionStatus] = mapped_column(
        _enum(CalendarConnectionStatus, "calendar_connection_status"),
        nullable=False,
        default=CalendarConnectionStatus.CONNECTED,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_calendar_tenant_provider"),
        Index("ix_calendar_connections_tenant_id", "tenant_id"),
    )


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    usage_type: Mapped[str] = mapped_column(String(60), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    cost_cents: Mapped[int | None] = mapped_column(BigInteger)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=sa_text("now()")
    )

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        Index("ix_usage_records_tenant_recorded", "tenant_id", "recorded_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Nullable: platform-level events (e.g. admin sign-in) have no tenant.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    actor_external_user_id: Mapped[str | None] = mapped_column(String(120))
    actor_role: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(120))
    before_redacted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_redacted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=sa_text("now()")
    )

    __table_args__ = (
        Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_logs_action", "action"),
    )


class ProviderEvent(Base):
    __tablename__ = "provider_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_redacted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[ProviderEventStatus] = mapped_column(
        _enum(ProviderEventStatus, "provider_event_status"),
        nullable=False,
        default=ProviderEventStatus.RECEIVED,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=sa_text("now()")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_provider_event"),
        Index("ix_provider_events_status", "status"),
    )


class ConfigVersion(TimestampMixin, Base):
    """Versioned configuration snapshots driving the approval workflow.

    Exactly one open (draft/pending_review) and at most one active row
    per tenant, enforced by partial unique indexes. The active row's
    payload is what approval applied onto the live config tables — the
    voice path reads those tables, never a draft.
    """

    __tablename__ = "config_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[ConfigVersionState] = mapped_column(
        _enum(ConfigVersionState, "config_version_state"),
        nullable=False,
        default=ConfigVersionState.DRAFT,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_config_version_tenant_version"),
        Index(
            "uq_config_versions_one_open",
            "tenant_id",
            unique=True,
            postgresql_where=sa_text("state IN ('draft', 'pending_review')"),
        ),
        Index(
            "uq_config_versions_one_active",
            "tenant_id",
            unique=True,
            postgresql_where=sa_text("state = 'active'"),
        ),
        Index("ix_config_versions_tenant_id", "tenant_id"),
    )


class SimulatorSession(TimestampMixin, Base):
    """Browser test-console session state.

    One row per simulator call; failure flags configure injected faults
    the engine and tools honor on subsequent turns. Simulator calls are
    flagged by calls.transport = browser_text and excluded from client
    dashboards and usage.
    """

    __tablename__ = "simulator_sessions"

    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    failure_flags: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    engine_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_simulator_sessions_tenant_id", "tenant_id"),)
