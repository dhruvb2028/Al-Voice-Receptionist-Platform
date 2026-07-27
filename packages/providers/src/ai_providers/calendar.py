"""Calendar provider interface (Google Calendar in production)."""

import uuid
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from ai_providers.errors import CredentialRevokedError, ProviderResponseError


class CalendarSlot(BaseModel):
    start: datetime
    end: datetime


class CalendarEvent(BaseModel):
    event_id: str
    start: datetime
    end: datetime
    summary: str
    cancelled: bool = False


@runtime_checkable
class CalendarProvider(Protocol):
    async def validate_connection(self) -> bool:
        """Cheap credential + calendar reachability check."""
        ...

    async def check_availability(
        self, *, window_start: datetime, window_end: datetime, duration_minutes: int
    ) -> list[CalendarSlot]:
        """Free slots inside the window. Only these may be offered to a
        caller — never invented availability."""
        ...

    async def revalidate_slot(self, *, start: datetime, end: datetime) -> bool:
        """Re-check one slot immediately before booking (race guard)."""
        ...

    async def create_event(
        self, *, start: datetime, end: datetime, summary: str, description: str
    ) -> CalendarEvent: ...

    async def cancel_event(self, *, event_id: str) -> None: ...

    async def fetch_event(self, *, event_id: str) -> CalendarEvent | None: ...


class MockCalendarProvider:
    """In-memory calendar with deterministic availability."""

    def __init__(self, *, free_slots: list[CalendarSlot] | None = None) -> None:
        self.free_slots = free_slots or []
        self.events: dict[str, CalendarEvent] = {}
        self.revoked = False

    def _check_credentials(self) -> None:
        if self.revoked:
            raise CredentialRevokedError("calendar access revoked", provider="mock-calendar")

    async def validate_connection(self) -> bool:
        self._check_credentials()
        return True

    async def check_availability(
        self, *, window_start: datetime, window_end: datetime, duration_minutes: int
    ) -> list[CalendarSlot]:
        self._check_credentials()
        return [
            slot
            for slot in self.free_slots
            if slot.start >= window_start
            and slot.end <= window_end
            and (slot.end - slot.start).total_seconds() >= duration_minutes * 60
            and not self._overlaps_event(slot)
        ]

    def _overlaps_event(self, slot: CalendarSlot) -> bool:
        return any(
            not event.cancelled and slot.start < event.end and event.start < slot.end
            for event in self.events.values()
        )

    async def revalidate_slot(self, *, start: datetime, end: datetime) -> bool:
        self._check_credentials()
        candidate = CalendarSlot(start=start, end=end)
        in_free = any(slot.start <= start and end <= slot.end for slot in self.free_slots)
        return in_free and not self._overlaps_event(candidate)

    async def create_event(
        self, *, start: datetime, end: datetime, summary: str, description: str
    ) -> CalendarEvent:
        self._check_credentials()
        if not await self.revalidate_slot(start=start, end=end):
            raise ProviderResponseError("slot is no longer available", provider="mock-calendar")
        event = CalendarEvent(
            event_id=f"evt_{uuid.uuid4().hex[:10]}", start=start, end=end, summary=summary
        )
        self.events[event.event_id] = event
        return event

    async def cancel_event(self, *, event_id: str) -> None:
        self._check_credentials()
        event = self.events.get(event_id)
        if event is not None:
            self.events[event_id] = event.model_copy(update={"cancelled": True})

    async def fetch_event(self, *, event_id: str) -> CalendarEvent | None:
        self._check_credentials()
        return self.events.get(event_id)
