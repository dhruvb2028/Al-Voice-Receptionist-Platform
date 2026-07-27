"""The six approved business tools.

Every fact the receptionist can act on flows through these tools;
results are authoritative over anything the model believes. The tenant
identity is bound at construction from trusted context — tool arguments
can never select a tenant.

Persistence is injected behind small protocols so the same toolkit runs
against the real database (voice service), and against dry-run stores
(browser simulator, tests).
"""

import hashlib
import time as time_module
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol
from zoneinfo import ZoneInfo

import structlog
from ai_providers.cache import CacheProvider
from ai_providers.calendar import CalendarProvider
from ai_providers.errors import ProviderError, ProviderTimeoutError
from pydantic import BaseModel

from ai_domain.config import ReceptionistConfig

if TYPE_CHECKING:
    from ai_domain.conversation import ToolRegistry

logger = structlog.get_logger()

AVAILABILITY_CACHE_TTL_SECONDS = 30
SLOT_STEP_MINUTES = 30


# --- persistence protocols ---------------------------------------------------


class BookingRecord(BaseModel):
    booking_id: str
    status: Literal["pending", "confirmed", "failed", "reconciliation_required"]
    already_existed: bool = False


class BookingPersistence(Protocol):
    async def create_pending(
        self,
        *,
        idempotency_key: str,
        service_name: str,
        slot_start: datetime,
        slot_end: datetime,
        customer_name: str,
        customer_phone: str,
        address: str,
        notes: str | None,
        timezone: str,
    ) -> BookingRecord:
        """Insert a pending booking; a duplicate idempotency key returns
        the existing record with ``already_existed=True``."""
        ...

    async def confirm(self, *, booking_id: str, calendar_event_id: str) -> None: ...

    async def mark_failed(self, *, booking_id: str) -> None: ...

    async def mark_reconciliation_required(
        self, *, booking_id: str, calendar_event_id: str | None
    ) -> None: ...


class MessagePersistence(Protocol):
    async def save_message(
        self,
        *,
        customer_name: str | None,
        customer_phone: str,
        problem: str,
        urgency: str,
        preferred_contact_time: str | None,
        original_question: str | None,
    ) -> str:
        """Persist the message; returns the message ID. Must raise on
        failure — a message is never silently dropped."""
        ...


class TransferExecutor(Protocol):
    async def transfer(self, *, destination_e164: str, timeout_seconds: int) -> bool:
        """Attempt a warm transfer; True when a human answered."""
        ...


class SMSSender(Protocol):
    async def send_template(
        self,
        *,
        to_e164: str,
        template: str,
        variables: dict[str, str],
        idempotency_key: str,
    ) -> Any: ...


# --- urgency classification --------------------------------------------------

# Deterministic emergency rules — these override any model judgment.
_EMERGENCY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gas_smell", ("gas smell", "smell gas", "smells like gas", "gas leak")),
    ("electrical_fire", ("electrical fire", "outlet on fire", "wire on fire", "on fire")),
    ("sparking_panel", ("sparking", "sparks from", "panel is buzzing and hot")),
    (
        "major_flooding",
        ("flooding", "burst pipe", "water everywhere", "water pouring", "uncontrolled water"),
    ),
    ("carbon_monoxide", ("carbon monoxide", "co detector", "co alarm")),
)

_URGENT_HINTS: tuple[str, ...] = (
    "no heat",
    "no hot water",
    "no water",
    "sewage",
    "leak",
    "overflow",
    "won't stop running",
    "no power",
)


class UrgencyResult(BaseModel):
    urgency: Literal["emergency", "urgent", "routine"]
    confidence: float
    reason_code: str


def classify_urgency(description: str, *, model_suggestion: str | None = None) -> UrgencyResult:
    """Deterministic rules first; the model's opinion is only a
    tiebreaker for the urgent/routine boundary."""
    lowered = description.lower()
    for reason_code, phrases in _EMERGENCY_RULES:
        if any(phrase in lowered for phrase in phrases):
            return UrgencyResult(urgency="emergency", confidence=1.0, reason_code=reason_code)
    if any(hint in lowered for hint in _URGENT_HINTS):
        return UrgencyResult(urgency="urgent", confidence=0.8, reason_code="urgent_keyword")
    if model_suggestion == "urgent":
        return UrgencyResult(urgency="urgent", confidence=0.6, reason_code="model_suggested")
    return UrgencyResult(urgency="routine", confidence=0.7, reason_code="default_routine")


# --- toolkit -----------------------------------------------------------------


@dataclass
class BusinessToolkit:
    """The six tools, bound to one tenant, one call, one configuration."""

    tenant_id: str  # trusted context — never from tool arguments
    call_id: str
    config: ReceptionistConfig
    calendar: CalendarProvider
    cache: CacheProvider
    bookings: BookingPersistence
    messages: MessagePersistence
    transfers: TransferExecutor | None = None
    sms: SMSSender | None = None
    #: consent recorded during the call (e.g. caller agreed to texts)
    sms_consent: bool = False
    tool_timeout_seconds: float = 4.0
    _availability_cache: dict[str, tuple[float, list[dict[str, str]]]] = field(default_factory=dict)

    # -- 1. check_availability ---------------------------------------------

    def _service(self, service_name: str) -> Any | None:
        wanted = service_name.strip().lower()
        for service in self.config.services:
            if service.active and service.name.strip().lower() == wanted:
                return service
        return None

    def _hours_for(self, day: date) -> tuple[time, time] | None:
        """Open window for a calendar date, honoring holiday overrides."""
        for override in self.config.holiday_overrides:
            if override.date == day.isoformat():
                if override.closed or not override.opens_at or not override.closes_at:
                    return None
                return override.opens_at, override.closes_at
        for entry in self.config.hours:
            if entry.weekday == day.weekday():
                if entry.closed or not entry.opens_at or not entry.closes_at:
                    return None
                return entry.opens_at, entry.closes_at
        return None

    async def check_availability(
        self,
        *,
        service_name: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        service = self._service(service_name)
        if service is None:
            return {"known": False, "reason": "unknown_service"}

        cache_key = f"{service_name}:{start_date}:{end_date}"
        cached = self._availability_cache.get(cache_key)
        now_monotonic = time_module.monotonic()
        if cached and now_monotonic - cached[0] < AVAILABILITY_CACHE_TTL_SECONDS:
            return {
                "known": True,
                "slots": cached[1],
                "timezone": self.config.identity.timezone,
                "source": "cache",
                "expires_in_seconds": int(
                    AVAILABILITY_CACHE_TTL_SECONDS - (now_monotonic - cached[0])
                ),
            }

        tz = ZoneInfo(self.config.identity.timezone)
        window_start = datetime.combine(date.fromisoformat(start_date), time.min, tz)
        window_end = datetime.combine(date.fromisoformat(end_date), time.max, tz)

        free = await self.calendar.check_availability(
            window_start=window_start,
            window_end=window_end,
            duration_minutes=service.duration_minutes,
        )

        # Only slots inside business hours (holidays included) survive;
        # nothing is ever invented beyond what the calendar returned.
        slots: list[dict[str, str]] = []
        duration = timedelta(minutes=service.duration_minutes)
        step = timedelta(minutes=SLOT_STEP_MINUTES)
        for block in free:
            cursor = block.start.astimezone(tz)
            block_end = block.end.astimezone(tz)
            while cursor + duration <= block_end:
                window = self._hours_for(cursor.date())
                if window is not None:
                    opens, closes = window
                    slot_end = cursor + duration
                    if cursor.time() >= opens and slot_end.time() <= closes:
                        slots.append(
                            {
                                "start": cursor.isoformat(),
                                "end": slot_end.isoformat(),
                            }
                        )
                cursor += step
                if len(slots) >= 20:
                    break
            if len(slots) >= 20:
                break

        self._availability_cache[cache_key] = (now_monotonic, slots)
        return {
            "known": True,
            "slots": slots,
            "timezone": self.config.identity.timezone,
            "source": "calendar",
            "expires_in_seconds": AVAILABILITY_CACHE_TTL_SECONDS,
        }

    # -- 2. book_appointment -----------------------------------------------

    def _idempotency_key(self, service_name: str, slot_start: str) -> str:
        raw = f"{self.call_id}:{service_name.strip().lower()}:{slot_start}"
        return hashlib.sha256(raw.encode()).hexdigest()[:40]

    async def book_appointment(
        self,
        *,
        customer_name: str,
        customer_phone: str,
        address: str,
        service_name: str,
        slot_start: str,
        slot_end: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        service = self._service(service_name)
        if service is None:
            return {"confirmed": False, "reason": "unknown_service"}

        start = datetime.fromisoformat(slot_start)
        end = datetime.fromisoformat(slot_end)
        idempotency_key = self._idempotency_key(service_name, slot_start)

        lock_name = f"booking:{self.tenant_id}:{slot_start}"
        token = await self.cache.acquire_lock(name=lock_name, ttl_seconds=30)
        if token is None:
            return {"confirmed": False, "reason": "slot_contended"}

        try:
            # Idempotency first: a replayed identical call must return the
            # original booking even when the slot now reads as taken
            # (usually taken by that very booking).
            record = await self.bookings.create_pending(
                idempotency_key=idempotency_key,
                service_name=service.name,
                slot_start=start,
                slot_end=end,
                customer_name=customer_name,
                customer_phone=customer_phone,
                address=address,
                notes=notes,
                timezone=self.config.identity.timezone,
            )
            if record.already_existed:
                # Duplicate tool call — the original booking stands.
                return {
                    "confirmed": record.status == "confirmed",
                    "booking_id": record.booking_id,
                    "duplicate": True,
                }

            if not await self.calendar.revalidate_slot(start=start, end=end):
                await self.bookings.mark_failed(booking_id=record.booking_id)
                return {"confirmed": False, "reason": "slot_no_longer_available"}

            try:
                event = await self.calendar.create_event(
                    start=start,
                    end=end,
                    summary=f"{service.name} — {customer_name}",
                    description=notes or "",
                )
            except ProviderError as exc:
                await self.bookings.mark_failed(booking_id=record.booking_id)
                logger.warning("booking_calendar_failed", error=exc.category)
                return {"confirmed": False, "reason": "calendar_error"}

            try:
                await self.bookings.confirm(
                    booking_id=record.booking_id, calendar_event_id=event.event_id
                )
            except Exception:
                # Partial failure: event exists, DB confirm failed —
                # reconciliation resolves in favor of the database.
                await self.bookings.mark_reconciliation_required(
                    booking_id=record.booking_id, calendar_event_id=event.event_id
                )
                logger.exception("booking_reconciliation_required")
                return {"confirmed": False, "reason": "reconciliation_required"}

            # Genuinely confirmed: database row + calendar event both exist.
            return {
                "confirmed": True,
                "booking_id": record.booking_id,
                "calendar_event_id": event.event_id,
                "slot_start": slot_start,
                "slot_end": slot_end,
            }
        finally:
            await self.cache.release_lock(name=lock_name, token=token)

    # -- 3. classify_urgency (module-level function, exposed as tool) ------

    # -- 4. transfer_to_human ----------------------------------------------

    async def transfer_to_human(self, *, reason: str) -> dict[str, Any]:
        destination = self.config.escalation.emergency_destination
        if reason != "emergency" and self.config.escalation.after_hours_destination:
            destination = self.config.escalation.after_hours_destination

        if self.transfers is None:
            return {
                "status": "failed",
                "message_fallback_required": True,
                "reason": "transfer_unavailable",
            }
        try:
            connected = await self.transfers.transfer(
                destination_e164=destination,
                timeout_seconds=self.config.escalation.transfer_timeout_seconds,
            )
        except ProviderTimeoutError:
            return {"status": "timed_out", "message_fallback_required": True}
        except ProviderError:
            return {"status": "failed", "message_fallback_required": True}
        if connected:
            return {"status": "connected", "message_fallback_required": False}
        return {"status": "failed", "message_fallback_required": True}

    # -- 5. take_message ----------------------------------------------------

    async def take_message(
        self,
        *,
        customer_name: str | None,
        customer_phone: str,
        problem: str,
        preferred_contact_time: str | None = None,
        original_question: str | None = None,
    ) -> dict[str, Any]:
        urgency = classify_urgency(problem)
        # Persistence MUST succeed before any notification is attempted;
        # notification dispatch belongs to the post-call worker.
        message_id = await self.messages.save_message(
            customer_name=customer_name,
            customer_phone=customer_phone,
            problem=problem,
            urgency=urgency.urgency,
            preferred_contact_time=preferred_contact_time,
            original_question=original_question,
        )
        return {"saved": True, "message_id": message_id, "urgency": urgency.urgency}

    # -- 6. send_sms ---------------------------------------------------------

    async def send_sms(
        self,
        *,
        template_id: str,
        variables: dict[str, str],
        to_e164: str,
    ) -> dict[str, Any]:
        if not self.sms_consent:
            return {"sent": False, "reason": "no_consent"}
        if self.sms is None:
            return {"sent": False, "reason": "sms_unavailable"}
        idempotency_key = f"{self.call_id}:{template_id}"
        try:
            result = await self.sms.send_template(
                to_e164=to_e164,
                template=template_id,
                variables=variables,
                idempotency_key=idempotency_key,
            )
        except ProviderError as exc:
            if exc.category == "duplicate_send":
                return {"sent": True, "duplicate": True}
            return {"sent": False, "reason": exc.category}
        return {"sent": True, "provider_message_id": result.provider_message_id}


# --- LLM tool registry -------------------------------------------------------


def build_business_tools(toolkit: BusinessToolkit) -> "ToolRegistry":
    """Registry combining the config-info tools with the six business
    tools, all bound to the toolkit's trusted tenant context."""
    from ai_providers.llm import ToolSpec

    from ai_domain.conversation import build_config_tools

    registry = build_config_tools(toolkit.config)

    async def _check_availability(arguments: dict[str, Any]) -> dict[str, Any]:
        return await toolkit.check_availability(
            service_name=str(arguments.get("service_name", "")),
            start_date=str(arguments.get("start_date", "")),
            end_date=str(arguments.get("end_date", "")),
        )

    async def _book_appointment(arguments: dict[str, Any]) -> dict[str, Any]:
        # tenant is bound in the toolkit; any tenant_id argument the
        # model invents is discarded here.
        return await toolkit.book_appointment(
            customer_name=str(arguments.get("customer_name", "")),
            customer_phone=str(arguments.get("customer_phone", "")),
            address=str(arguments.get("address", "")),
            service_name=str(arguments.get("service_name", "")),
            slot_start=str(arguments.get("slot_start", "")),
            slot_end=str(arguments.get("slot_end", "")),
            notes=arguments.get("notes"),
        )

    async def _classify_urgency(arguments: dict[str, Any]) -> dict[str, Any]:
        result = classify_urgency(
            str(arguments.get("description", "")),
            model_suggestion=arguments.get("model_suggestion"),
        )
        return result.model_dump()

    async def _transfer(arguments: dict[str, Any]) -> dict[str, Any]:
        return await toolkit.transfer_to_human(reason=str(arguments.get("reason", "other")))

    async def _take_message(arguments: dict[str, Any]) -> dict[str, Any]:
        return await toolkit.take_message(
            customer_name=arguments.get("customer_name"),
            customer_phone=str(arguments.get("customer_phone", "")),
            problem=str(arguments.get("problem", "")),
            preferred_contact_time=arguments.get("preferred_contact_time"),
            original_question=arguments.get("original_question"),
        )

    async def _send_sms(arguments: dict[str, Any]) -> dict[str, Any]:
        return await toolkit.send_sms(
            template_id=str(arguments.get("template_id", "")),
            variables={str(k): str(v) for k, v in (arguments.get("variables") or {}).items()},
            to_e164=str(arguments.get("to_e164", "")),
        )

    registry.register(
        ToolSpec(
            name="check_availability",
            description="List real appointment slots for a service between two dates.",
            parameters={
                "type": "object",
                "properties": {
                    "service_name": {"type": "string"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["service_name", "start_date", "end_date"],
            },
        ),
        _check_availability,
    )
    registry.register(
        ToolSpec(
            name="book_appointment",
            description=(
                "Book a previously offered slot. Only call after the caller "
                "confirmed name, phone, address, service, and slot."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "customer_phone": {"type": "string"},
                    "address": {"type": "string"},
                    "service_name": {"type": "string"},
                    "slot_start": {"type": "string"},
                    "slot_end": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "customer_name",
                    "customer_phone",
                    "address",
                    "service_name",
                    "slot_start",
                    "slot_end",
                ],
            },
        ),
        _book_appointment,
    )
    registry.register(
        ToolSpec(
            name="classify_urgency",
            description="Classify how urgent the caller's problem is.",
            parameters={
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "model_suggestion": {
                        "type": "string",
                        "enum": ["emergency", "urgent", "routine"],
                    },
                },
                "required": ["description"],
            },
        ),
        _classify_urgency,
    )
    registry.register(
        ToolSpec(
            name="transfer_to_human",
            description=(
                "Transfer the caller to a human. Use for emergencies and explicit requests."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": ["emergency", "human_request", "intent_failure", "other"],
                    }
                },
                "required": ["reason"],
            },
        ),
        _transfer,
    )
    registry.register(
        ToolSpec(
            name="take_message",
            description="Save a message for the business to call back.",
            parameters={
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "customer_phone": {"type": "string"},
                    "problem": {"type": "string"},
                    "preferred_contact_time": {"type": "string"},
                    "original_question": {"type": "string"},
                },
                "required": ["customer_phone", "problem"],
            },
        ),
        _take_message,
    )
    registry.register(
        ToolSpec(
            name="send_sms",
            description="Send an approved template SMS to the caller (requires consent).",
            parameters={
                "type": "object",
                "properties": {
                    "template_id": {"type": "string"},
                    "variables": {"type": "object"},
                    "to_e164": {"type": "string"},
                },
                "required": ["template_id", "to_e164"],
            },
        ),
        _send_sms,
    )
    return registry
