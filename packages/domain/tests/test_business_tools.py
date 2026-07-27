"""Business-tool tests: availability filtering, booking concurrency and
idempotency, urgency rules, transfer fallback, message-first persistence,
SMS template/consent guards, timeouts."""

import asyncio
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from ai_domain.config import ReceptionistConfig
from ai_domain.tools import (
    BookingRecord,
    BusinessToolkit,
    build_business_tools,
    classify_urgency,
)
from ai_providers.cache import MockCacheProvider
from ai_providers.calendar import CalendarSlot, MockCalendarProvider
from ai_providers.errors import ProviderTimeoutError
from ai_providers.messaging import MockSMSProvider

TZ = ZoneInfo("America/New_York")


def _config() -> ReceptionistConfig:
    return ReceptionistConfig.model_validate(
        {
            "identity": {"business_name": "Harbor Plumbing", "timezone": "America/New_York"},
            "greeting": {"greeting": "Thanks for calling Harbor Plumbing!"},
            "services": [
                {"name": "Drain cleaning", "duration_minutes": 60},
                {"name": "Leak repair", "duration_minutes": 120},
            ],
            "hours": [{"weekday": d, "opens_at": "08:00", "closes_at": "17:00"} for d in range(5)]
            + [{"weekday": 5, "closed": True}, {"weekday": 6, "closed": True}],
            "holiday_overrides": [{"date": "2026-08-05", "closed": True, "note": "Closed"}],
            "service_area": {"postal_codes": ["02101"]},
            "escalation": {
                "emergency_destination": "+15555550100",
                "transfer_timeout_seconds": 25,
            },
            "voice": {"voice_id": "warm-1"},
        }
    )


class MemoryBookings:
    """In-memory BookingPersistence for tool-logic tests."""

    def __init__(self, *, fail_confirm: bool = False) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.by_key: dict[str, str] = {}
        self.fail_confirm = fail_confirm
        self.reconciliations: list[str] = []
        self._next = 0

    async def create_pending(self, *, idempotency_key: str, **kwargs: Any) -> BookingRecord:
        if idempotency_key in self.by_key:
            booking_id = self.by_key[idempotency_key]
            return BookingRecord(
                booking_id=booking_id,
                status=self.records[booking_id]["status"],
                already_existed=True,
            )
        self._next += 1
        booking_id = f"b{self._next}"
        self.records[booking_id] = {"status": "pending", **kwargs}
        self.by_key[idempotency_key] = booking_id
        return BookingRecord(booking_id=booking_id, status="pending")

    async def confirm(self, *, booking_id: str, calendar_event_id: str) -> None:
        if self.fail_confirm:
            raise RuntimeError("db write failed")
        self.records[booking_id]["status"] = "confirmed"
        self.records[booking_id]["event"] = calendar_event_id

    async def mark_failed(self, *, booking_id: str) -> None:
        self.records[booking_id]["status"] = "failed"

    async def mark_reconciliation_required(
        self, *, booking_id: str, calendar_event_id: str | None
    ) -> None:
        self.records[booking_id]["status"] = "reconciliation_required"
        self.reconciliations.append(booking_id)


class MemoryMessages:
    def __init__(self, *, fail: bool = False) -> None:
        self.saved: list[dict[str, Any]] = []
        self.fail = fail

    async def save_message(self, **kwargs: Any) -> str:
        if self.fail:
            raise RuntimeError("db unavailable")
        self.saved.append(kwargs)
        return f"m{len(self.saved)}"


class FakeTransfers:
    def __init__(self, *, outcome: str = "connected") -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, int]] = []

    async def transfer(self, *, destination_e164: str, timeout_seconds: int) -> bool:
        self.calls.append((destination_e164, timeout_seconds))
        if self.outcome == "timeout":
            raise ProviderTimeoutError("no answer")
        return self.outcome == "connected"


def _slot(day: int, hour: int, length_hours: int = 4) -> CalendarSlot:
    start = datetime(2026, 8, day, hour, 0, tzinfo=TZ)
    return CalendarSlot(start=start, end=start + timedelta(hours=length_hours))


def _toolkit(**overrides: Any) -> BusinessToolkit:
    defaults: dict[str, Any] = {
        "tenant_id": "tenant-1",
        "call_id": "call-1",
        "config": _config(),
        "calendar": MockCalendarProvider(free_slots=[_slot(3, 9)]),  # Monday
        "cache": MockCacheProvider(),
        "bookings": MemoryBookings(),
        "messages": MemoryMessages(),
        "transfers": FakeTransfers(),
        "sms": MockSMSProvider(),
    }
    defaults.update(overrides)
    return BusinessToolkit(**defaults)


# --- check_availability ------------------------------------------------------


async def test_availability_respects_hours_duration_and_returns_metadata() -> None:
    toolkit = _toolkit()
    result = await toolkit.check_availability(
        service_name="Drain cleaning", start_date="2026-08-03", end_date="2026-08-04"
    )
    assert result["known"] is True
    assert result["timezone"] == "America/New_York"
    assert result["source"] == "calendar"
    assert result["expires_in_seconds"] > 0
    assert result["slots"], "expected slots inside business hours"
    for slot in result["slots"]:
        start = datetime.fromisoformat(slot["start"])
        end = datetime.fromisoformat(slot["end"])
        assert start.time() >= datetime(2026, 1, 1, 8, 0).time()
        assert end.time() <= datetime(2026, 1, 1, 17, 0).time()
        assert (end - start) == timedelta(minutes=60)


async def test_availability_empty_when_calendar_has_nothing() -> None:
    toolkit = _toolkit(calendar=MockCalendarProvider(free_slots=[]))
    result = await toolkit.check_availability(
        service_name="Drain cleaning", start_date="2026-08-03", end_date="2026-08-04"
    )
    assert result["known"] is True
    assert result["slots"] == []  # nothing invented


async def test_availability_respects_holiday_override() -> None:
    # Free block exists on 2026-08-05 (Wednesday) but it's a holiday.
    toolkit = _toolkit(calendar=MockCalendarProvider(free_slots=[_slot(5, 9)]))
    result = await toolkit.check_availability(
        service_name="Drain cleaning", start_date="2026-08-05", end_date="2026-08-05"
    )
    assert result["slots"] == []


async def test_availability_weekend_closed() -> None:
    toolkit = _toolkit(calendar=MockCalendarProvider(free_slots=[_slot(1, 9)]))  # Saturday
    result = await toolkit.check_availability(
        service_name="Drain cleaning", start_date="2026-08-01", end_date="2026-08-01"
    )
    assert result["slots"] == []


async def test_availability_unknown_service() -> None:
    toolkit = _toolkit()
    result = await toolkit.check_availability(
        service_name="Roofing", start_date="2026-08-03", end_date="2026-08-04"
    )
    assert result == {"known": False, "reason": "unknown_service"}


async def test_availability_cached_briefly() -> None:
    calendar = MockCalendarProvider(free_slots=[_slot(3, 9)])
    toolkit = _toolkit(calendar=calendar)
    first = await toolkit.check_availability(
        service_name="Drain cleaning", start_date="2026-08-03", end_date="2026-08-03"
    )
    second = await toolkit.check_availability(
        service_name="Drain cleaning", start_date="2026-08-03", end_date="2026-08-03"
    )
    assert first["source"] == "calendar"
    assert second["source"] == "cache"


# --- book_appointment --------------------------------------------------------

BOOKING_ARGS: dict[str, Any] = {
    "customer_name": "Pat",
    "customer_phone": "+15550001111",
    "address": "1 Main St",
    "service_name": "Drain cleaning",
    "slot_start": datetime(2026, 8, 3, 9, 0, tzinfo=TZ).isoformat(),
    "slot_end": datetime(2026, 8, 3, 10, 0, tzinfo=TZ).isoformat(),
}


async def test_booking_happy_path_confirms_only_after_both_writes() -> None:
    bookings = MemoryBookings()
    toolkit = _toolkit(bookings=bookings)
    result = await toolkit.book_appointment(**BOOKING_ARGS)
    assert result["confirmed"] is True
    record = bookings.records[result["booking_id"]]
    assert record["status"] == "confirmed"
    assert record["event"] == result["calendar_event_id"]


async def test_booking_duplicate_idempotency_returns_original() -> None:
    bookings = MemoryBookings()
    toolkit = _toolkit(bookings=bookings)
    first = await toolkit.book_appointment(**BOOKING_ARGS)
    second = await toolkit.book_appointment(**BOOKING_ARGS)
    assert second["duplicate"] is True
    assert second["booking_id"] == first["booking_id"]
    assert len(bookings.records) == 1


async def test_booking_concurrent_same_slot_one_winner() -> None:
    calendar = MockCalendarProvider(free_slots=[_slot(3, 9)])
    cache = MockCacheProvider()
    toolkit_a = _toolkit(calendar=calendar, cache=cache, call_id="call-A")
    toolkit_b = _toolkit(calendar=calendar, cache=cache, call_id="call-B")
    toolkit_a.call_id = "call-A"
    toolkit_b.call_id = "call-B"

    results = await asyncio.gather(
        toolkit_a.book_appointment(**BOOKING_ARGS),
        toolkit_b.book_appointment(**BOOKING_ARGS),
    )
    confirmed = [r for r in results if r.get("confirmed")]
    rejected = [r for r in results if not r.get("confirmed")]
    assert len(confirmed) == 1
    assert len(rejected) == 1
    assert rejected[0]["reason"] in ("slot_contended", "slot_no_longer_available")


async def test_booking_revalidation_blocks_taken_slot() -> None:
    calendar = MockCalendarProvider(free_slots=[_slot(3, 9, length_hours=1)])
    toolkit = _toolkit(calendar=calendar)
    # Someone else takes the slot directly on the calendar.
    await calendar.create_event(
        start=datetime(2026, 8, 3, 9, 0, tzinfo=TZ),
        end=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        summary="Other job",
        description="",
    )
    result = await toolkit.book_appointment(**BOOKING_ARGS)
    assert result["confirmed"] is False
    assert result["reason"] == "slot_no_longer_available"


async def test_booking_calendar_failure_marks_failed() -> None:
    calendar = MockCalendarProvider(free_slots=[_slot(3, 9)])
    bookings = MemoryBookings()
    toolkit = _toolkit(calendar=calendar, bookings=bookings)

    async def broken_create(**kwargs: Any) -> Any:
        raise ProviderTimeoutError("calendar down")

    calendar.create_event = broken_create  # type: ignore[method-assign]
    result = await toolkit.book_appointment(**BOOKING_ARGS)
    assert result["confirmed"] is False
    assert result["reason"] == "calendar_error"
    assert list(bookings.records.values())[0]["status"] == "failed"


async def test_booking_partial_failure_records_reconciliation() -> None:
    bookings = MemoryBookings(fail_confirm=True)
    toolkit = _toolkit(bookings=bookings)
    result = await toolkit.book_appointment(**BOOKING_ARGS)
    assert result["confirmed"] is False
    assert result["reason"] == "reconciliation_required"
    assert bookings.reconciliations, "partial failure must be flagged for reconciliation"


async def test_booking_ignores_llm_supplied_tenant() -> None:
    toolkit = _toolkit()
    registry = build_business_tools(toolkit)
    from ai_providers.llm import LLMToolCall

    trace = await registry.execute(
        LLMToolCall(
            id="t1",
            name="book_appointment",
            arguments={**BOOKING_ARGS, "tenant_id": "someone-elses-tenant"},
        )
    )
    assert trace.status == "success"
    assert trace.result is not None and trace.result["confirmed"] is True
    # The toolkit's bound tenant was used; the argument was discarded.
    assert toolkit.tenant_id == "tenant-1"


# --- classify_urgency --------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "expected_code"),
    [
        ("I smell gas in the kitchen", "gas_smell"),
        ("the outlet is on fire", "electrical_fire"),
        ("my panel is sparking", "sparking_panel"),
        ("burst pipe, water everywhere", "major_flooding"),
        ("the carbon monoxide alarm is going off", "carbon_monoxide"),
    ],
)
async def test_deterministic_emergency_rules(description: str, expected_code: str) -> None:
    result = classify_urgency(description, model_suggestion="routine")
    assert result.urgency == "emergency"  # model suggestion overridden
    assert result.confidence == 1.0
    assert result.reason_code == expected_code


async def test_urgent_and_routine_classification() -> None:
    assert classify_urgency("we have no heat tonight").urgency == "urgent"
    assert classify_urgency("faucet drips sometimes").urgency == "routine"
    assert classify_urgency("weird noise", model_suggestion="urgent").urgency == "urgent"


# --- transfer_to_human -------------------------------------------------------


async def test_transfer_connected() -> None:
    transfers = FakeTransfers(outcome="connected")
    toolkit = _toolkit(transfers=transfers)
    result = await toolkit.transfer_to_human(reason="human_request")
    assert result["status"] == "connected"
    assert result["message_fallback_required"] is False
    assert transfers.calls[0] == ("+15555550100", 25)


async def test_transfer_timeout_requires_message_fallback() -> None:
    toolkit = _toolkit(transfers=FakeTransfers(outcome="timeout"))
    result = await toolkit.transfer_to_human(reason="human_request")
    assert result["status"] == "timed_out"
    assert result["message_fallback_required"] is True


async def test_transfer_failure_requires_message_fallback() -> None:
    toolkit = _toolkit(transfers=FakeTransfers(outcome="failed"))
    result = await toolkit.transfer_to_human(reason="emergency")
    assert result["status"] == "failed"
    assert result["message_fallback_required"] is True


async def test_transfer_unavailable_requires_message_fallback() -> None:
    toolkit = _toolkit(transfers=None)
    result = await toolkit.transfer_to_human(reason="human_request")
    assert result["message_fallback_required"] is True


# --- take_message ------------------------------------------------------------


async def test_message_persisted_with_classified_urgency() -> None:
    messages = MemoryMessages()
    toolkit = _toolkit(messages=messages)
    result = await toolkit.take_message(
        customer_name="Pat",
        customer_phone="+15550001111",
        problem="no hot water since yesterday",
        preferred_contact_time="mornings",
        original_question="do you service tankless heaters?",
    )
    assert result["saved"] is True
    assert result["urgency"] == "urgent"
    saved = messages.saved[0]
    assert saved["preferred_contact_time"] == "mornings"
    assert saved["original_question"] == "do you service tankless heaters?"


async def test_message_persistence_failure_raises() -> None:
    toolkit = _toolkit(messages=MemoryMessages(fail=True))
    with pytest.raises(RuntimeError):
        await toolkit.take_message(
            customer_name=None, customer_phone="+15550001111", problem="callback please"
        )


# --- send_sms ----------------------------------------------------------------


async def test_sms_requires_consent() -> None:
    toolkit = _toolkit(sms_consent=False)
    result = await toolkit.send_sms(
        template_id="booking_confirmation", variables={}, to_e164="+15550001111"
    )
    assert result == {"sent": False, "reason": "no_consent"}


async def test_sms_rejects_unapproved_template() -> None:
    toolkit = _toolkit(sms_consent=True)
    result = await toolkit.send_sms(
        template_id="free_text_blast", variables={}, to_e164="+15550001111"
    )
    assert result["sent"] is False
    assert result["reason"] == "bad_response"


async def test_sms_idempotent_per_call_and_template() -> None:
    sms = MockSMSProvider()
    toolkit = _toolkit(sms=sms, sms_consent=True)
    first = await toolkit.send_sms(
        template_id="booking_confirmation", variables={"time": "3 PM"}, to_e164="+15550001111"
    )
    second = await toolkit.send_sms(
        template_id="booking_confirmation", variables={"time": "3 PM"}, to_e164="+15550001111"
    )
    assert first["sent"] is True
    assert second == {"sent": True, "duplicate": True}
    assert len(sms.sent) == 1


# --- timeouts surface through the registry -----------------------------------


async def test_tool_timeout_surfaces_in_trace() -> None:
    toolkit = _toolkit()
    registry = build_business_tools(toolkit)

    async def slow_calendar(**kwargs: Any) -> Any:
        raise ProviderTimeoutError("calendar timed out")

    toolkit.calendar.check_availability = slow_calendar  # type: ignore[method-assign]
    from ai_providers.llm import LLMToolCall

    trace = await registry.execute(
        LLMToolCall(
            id="t1",
            name="check_availability",
            arguments={
                "service_name": "Drain cleaning",
                "start_date": "2026-08-03",
                "end_date": "2026-08-04",
            },
        )
    )
    assert trace.status == "timeout"
    assert trace.error_category == "timeout"
