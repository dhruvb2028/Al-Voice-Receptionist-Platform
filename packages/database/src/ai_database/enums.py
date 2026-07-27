"""Domain enums persisted as PostgreSQL native enum types.

Values are lowercase strings; adding a value is an Alembic migration
(``ALTER TYPE ... ADD VALUE``), renaming one is a data migration —
treat additions as append-only.
"""

from enum import StrEnum


class TenantStatus(StrEnum):
    ONBOARDING = "onboarding"
    TESTING = "testing"
    ACTIVE = "active"
    PAUSED = "paused"
    SUSPENDED = "suspended"
    CHURNED = "churned"


class MemberRole(StrEnum):
    CLIENT_OWNER = "client_owner"
    CLIENT_STAFF = "client_staff"


class MemberStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    DISABLED = "disabled"


class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallOutcome(StrEnum):
    BOOKED = "booked"
    MESSAGE_TAKEN = "message_taken"
    TRANSFERRED = "transferred"
    ANSWERED_INQUIRY = "answered_inquiry"
    CALLER_HANGUP = "caller_hangup"
    FAILED = "failed"


class Urgency(StrEnum):
    EMERGENCY = "emergency"
    URGENT = "urgent"
    ROUTINE = "routine"


class RecordingStatus(StrEnum):
    DISABLED = "disabled"
    IN_PROGRESS = "in_progress"
    PENDING_FETCH = "pending_fetch"
    STORED = "stored"
    DELETED = "deleted"
    FAILED = "failed"


class TranscriptStatus(StrEnum):
    PENDING = "pending"
    PARTIAL = "partial"
    COMPLETE = "complete"
    FAILED = "failed"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"


class TurnRole(StrEnum):
    CALLER = "caller"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ToolExecutionStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


class BookingStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class ReconciliationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    RESOLVED = "resolved"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class EscalationReason(StrEnum):
    EMERGENCY = "emergency"
    HUMAN_REQUEST = "human_request"
    INTENT_FAILURE = "intent_failure"
    SYSTEM_ERROR = "system_error"


class EscalationStatus(StrEnum):
    INITIATED = "initiated"
    CONNECTED = "connected"
    FAILED = "failed"
    FELL_BACK_TO_MESSAGE = "fell_back_to_message"


class GuardrailType(StrEnum):
    PRICE_INVENTION = "price_invention"
    SERVICE_INVENTION = "service_invention"
    AVAILABILITY_INVENTION = "availability_invention"
    OFF_SCOPE = "off_scope"
    PROMPT_INJECTION = "prompt_injection"
    SENSITIVE_CONTENT = "sensitive_content"
    BOOKING_CONFIRMATION = "booking_confirmation"
    EMERGENCY = "emergency"
    HUMAN_REQUEST = "human_request"
    SYSTEM_ERROR = "system_error"
    INTENT_FAILURE = "intent_failure"
    MAX_DURATION = "max_duration"


class GuardrailAction(StrEnum):
    BLOCKED = "blocked"
    REWRITTEN = "rewritten"
    ESCALATED = "escalated"
    LOGGED = "logged"


class CalendarConnectionStatus(StrEnum):
    CONNECTED = "connected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class ProviderEventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"
    IGNORED = "ignored"


class ConfigVersionState(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class CallTransport(StrEnum):
    PHONE = "phone"
    BROWSER_TEXT = "browser_text"


class NotificationType(StrEnum):
    NEW_BOOKING = "new_booking"
    EMERGENCY_ESCALATION = "emergency_escalation"
    URGENT_MESSAGE = "urgent_message"
    FAILED_CALL = "failed_call"
    DAILY_SUMMARY = "daily_summary"
    WEEKLY_REPORT = "weekly_report"
    CALENDAR_DISCONNECTED = "calendar_disconnected"


class NotificationChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    #: recipient opted out, or consent was never granted
    SUPPRESSED = "suppressed"


class ConsentStatus(StrEnum):
    #: never asked — the safe default in every jurisdiction
    UNKNOWN = "unknown"
    GRANTED = "granted"
    REVOKED = "revoked"
