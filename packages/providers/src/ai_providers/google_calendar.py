"""Google Calendar provider.

Implements the CalendarProvider contract against the Google Calendar
API with tenant-specific OAuth credentials: automatic access-token
refresh, revocation detection, freebusy-based availability with buffer
time, minimum notice, and a maximum future window, and events tagged
with internal identifiers for reconciliation.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from ai_providers.calendar import CalendarEvent, CalendarSlot
from ai_providers.errors import (
    CredentialRevokedError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = structlog.get_logger()

GOOGLE_API_BASE = "https://www.googleapis.com/calendar/v3"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — public endpoint
SOURCE_MARKER = "ai-receptionist"

#: notified when a refresh produces a new access token so the caller can
#: persist it (encrypted) — provider stays storage-agnostic
TokenSaver = Callable[[str, datetime], Awaitable[None]]


class GoogleCalendarAuth:
    """Holds tenant OAuth credentials and refreshes on expiry."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
        token_expires_at: datetime | None,
        on_token_refreshed: TokenSaver | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expires_at = token_expires_at
        self.on_token_refreshed = on_token_refreshed
        self._http = http or httpx.AsyncClient(timeout=10.0)

    async def bearer(self) -> str:
        now = datetime.now(UTC)
        if self.token_expires_at is None or self.token_expires_at <= now + timedelta(seconds=60):
            await self._refresh()
        return self.access_token

    async def _refresh(self) -> None:
        try:
            response = await self._http.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(str(exc), provider="google-calendar") from exc

        if response.status_code == 400 and "invalid_grant" in response.text:
            # The tenant revoked our access — requires admin reconnection.
            raise CredentialRevokedError("google access revoked", provider="google-calendar")
        if response.status_code != 200:
            raise ProviderUnavailableError(
                f"token refresh failed ({response.status_code})",
                provider="google-calendar",
            )
        payload = response.json()
        self.access_token = payload["access_token"]
        self.token_expires_at = datetime.now(UTC) + timedelta(
            seconds=int(payload.get("expires_in", 3600))
        )
        if self.on_token_refreshed is not None:
            await self.on_token_refreshed(self.access_token, self.token_expires_at)
        logger.info("google_token_refreshed")


class GoogleCalendarProvider:
    """CalendarProvider against one tenant's connected calendar."""

    def __init__(
        self,
        *,
        auth: GoogleCalendarAuth,
        calendar_id: str,
        buffer_minutes: int = 15,
        minimum_notice_hours: int = 2,
        max_future_days: int = 30,
        timeout_seconds: float = 6.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._auth = auth
        self._calendar_id = calendar_id
        self.buffer_minutes = buffer_minutes
        self.minimum_notice_hours = minimum_notice_hours
        self.max_future_days = max_future_days
        self._timeout = timeout_seconds
        self._http = http or httpx.AsyncClient(base_url=GOOGLE_API_BASE, timeout=timeout_seconds)
        # Injected clients (shared with the OAuth flow) may have no base
        # URL; prefix the API base so relative paths always resolve.
        self._prefix = "" if str(self._http.base_url) else GOOGLE_API_BASE

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = await self._auth.bearer()
        try:
            response = await self._http.request(
                method,
                f"{self._prefix}{path}",
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "calendar API timed out", provider="google-calendar"
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(str(exc), provider="google-calendar") from exc

        if response.status_code in (401, 403):
            raise CredentialRevokedError(
                f"calendar access denied ({response.status_code})",
                provider="google-calendar",
            )
        if response.status_code == 410:
            raise ProviderResponseError("calendar gone", provider="google-calendar")
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"calendar server error ({response.status_code})",
                provider="google-calendar",
            )
        if response.status_code not in (200, 204):
            raise ProviderResponseError(
                f"unexpected calendar status {response.status_code}",
                provider="google-calendar",
            )
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()  # type: ignore[no-any-return]

    # -- health --------------------------------------------------------------

    async def validate_connection(self) -> bool:
        await self._request("GET", f"/calendars/{self._calendar_id}")
        return True

    # -- availability --------------------------------------------------------

    def _effective_window(
        self, window_start: datetime, window_end: datetime
    ) -> tuple[datetime, datetime]:
        now = datetime.now(UTC)
        earliest = now + timedelta(hours=self.minimum_notice_hours)
        latest = now + timedelta(days=self.max_future_days)
        return max(window_start, earliest), min(window_end, latest)

    async def check_availability(
        self, *, window_start: datetime, window_end: datetime, duration_minutes: int
    ) -> list[CalendarSlot]:
        start, end = self._effective_window(window_start, window_end)
        if start >= end:
            return []

        payload = await self._request(
            "POST",
            "/freeBusy",
            json={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": self._calendar_id}],
            },
        )
        calendars = payload.get("calendars", {})
        busy_raw = calendars.get(self._calendar_id, {}).get("busy", [])
        buffer = timedelta(minutes=self.buffer_minutes)
        busy: list[tuple[datetime, datetime]] = sorted(
            (
                datetime.fromisoformat(b["start"]) - buffer,
                datetime.fromisoformat(b["end"]) + buffer,
            )
            for b in busy_raw
        )

        # Free gaps between buffered busy blocks, long enough for the service.
        slots: list[CalendarSlot] = []
        needed = timedelta(minutes=duration_minutes)
        cursor = start
        for busy_start, busy_end in busy:
            if busy_start > cursor and busy_start - cursor >= needed:
                slots.append(CalendarSlot(start=cursor, end=busy_start))
            cursor = max(cursor, busy_end)
        if end > cursor and end - cursor >= needed:
            slots.append(CalendarSlot(start=cursor, end=end))
        return slots

    async def revalidate_slot(self, *, start: datetime, end: datetime) -> bool:
        payload = await self._request(
            "POST",
            "/freeBusy",
            json={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": self._calendar_id}],
            },
        )
        busy = payload.get("calendars", {}).get(self._calendar_id, {}).get("busy", [])
        return len(busy) == 0

    # -- events ---------------------------------------------------------------

    async def create_event(
        self,
        *,
        start: datetime,
        end: datetime,
        summary: str,
        description: str,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        address: str | None = None,
        call_id: str | None = None,
        booking_id: str | None = None,
    ) -> CalendarEvent:
        body: dict[str, Any] = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
            "extendedProperties": {
                "private": {
                    "source": SOURCE_MARKER,
                    **({"call_id": call_id} if call_id else {}),
                    **({"booking_id": booking_id} if booking_id else {}),
                    **({"customer_name": customer_name} if customer_name else {}),
                    **({"customer_phone": customer_phone} if customer_phone else {}),
                }
            },
        }
        if address:
            body["location"] = address

        payload = await self._request("POST", f"/calendars/{self._calendar_id}/events", json=body)
        event_id = payload.get("id")
        if not event_id:
            raise ProviderResponseError("event creation returned no id", provider="google-calendar")
        return CalendarEvent(event_id=event_id, start=start, end=end, summary=summary)

    async def cancel_event(self, *, event_id: str) -> None:
        await self._request("DELETE", f"/calendars/{self._calendar_id}/events/{event_id}")

    async def fetch_event(self, *, event_id: str) -> CalendarEvent | None:
        try:
            payload = await self._request(
                "GET", f"/calendars/{self._calendar_id}/events/{event_id}"
            )
        except ProviderResponseError:
            return None
        if payload.get("status") == "cancelled":
            return CalendarEvent(
                event_id=event_id,
                start=datetime.fromisoformat(payload["start"]["dateTime"]),
                end=datetime.fromisoformat(payload["end"]["dateTime"]),
                summary=payload.get("summary", ""),
                cancelled=True,
            )
        return CalendarEvent(
            event_id=event_id,
            start=datetime.fromisoformat(payload["start"]["dateTime"]),
            end=datetime.fromisoformat(payload["end"]["dateTime"]),
            summary=payload.get("summary", ""),
        )
