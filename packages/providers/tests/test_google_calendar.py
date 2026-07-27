"""Google Calendar provider tests against mocked HTTP transports."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from ai_providers.errors import (
    CredentialRevokedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ai_providers.google_calendar import (
    SOURCE_MARKER,
    GoogleCalendarAuth,
    GoogleCalendarProvider,
)

NOW = datetime.now(UTC)


def _auth(handler: Any = None, *, expires_in_future: bool = True) -> GoogleCalendarAuth:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler)) if handler else None
    return GoogleCalendarAuth(
        client_id="cid",
        client_secret="secret",
        access_token="access-1",
        refresh_token="refresh-1",
        token_expires_at=NOW + timedelta(hours=1)
        if expires_in_future
        else NOW - timedelta(hours=1),
        http=http,
    )


def _provider(
    api_handler: Any,
    *,
    auth: GoogleCalendarAuth | None = None,
    **kwargs: Any,
) -> GoogleCalendarProvider:
    return GoogleCalendarProvider(
        auth=auth or _auth(),
        calendar_id="primary",
        http=httpx.AsyncClient(transport=httpx.MockTransport(api_handler), base_url="https://mock"),
        **kwargs,
    )


# --- token refresh -----------------------------------------------------------


async def test_refresh_on_expiry_and_saver_notified() -> None:
    saved: list[tuple[str, datetime]] = []

    def token_handler(request: httpx.Request) -> httpx.Response:
        assert b"grant_type=refresh_token" in request.content
        return httpx.Response(200, json={"access_token": "access-2", "expires_in": 3600})

    auth = _auth(token_handler, expires_in_future=False)

    async def saver(token: str, expires: datetime) -> None:
        saved.append((token, expires))

    auth.on_token_refreshed = saver
    token = await auth.bearer()
    assert token == "access-2"
    assert saved and saved[0][0] == "access-2"


async def test_refresh_invalid_grant_is_revocation() -> None:
    def token_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error": "invalid_grant"}')

    auth = _auth(token_handler, expires_in_future=False)
    with pytest.raises(CredentialRevokedError):
        await auth.bearer()


async def test_valid_token_not_refreshed() -> None:
    def token_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("refresh must not be called")

    auth = _auth(token_handler, expires_in_future=True)
    assert await auth.bearer() == "access-1"


# --- availability math -------------------------------------------------------


async def test_availability_subtracts_busy_with_buffer() -> None:
    window_start = NOW + timedelta(days=1)
    busy_start = window_start + timedelta(hours=4)
    busy_end = busy_start + timedelta(hours=1)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/freeBusy")
        return httpx.Response(
            200,
            json={
                "calendars": {
                    "primary": {
                        "busy": [{"start": busy_start.isoformat(), "end": busy_end.isoformat()}]
                    }
                }
            },
        )

    provider = _provider(handler, buffer_minutes=15, minimum_notice_hours=0)
    slots = await provider.check_availability(
        window_start=window_start,
        window_end=window_start + timedelta(hours=8),
        duration_minutes=60,
    )
    assert len(slots) == 2
    # First free block ends 15 min (buffer) before the busy block.
    assert slots[0].end == busy_start - timedelta(minutes=15)
    # Second free block starts 15 min after the busy block.
    assert slots[1].start == busy_end + timedelta(minutes=15)


async def test_availability_respects_minimum_notice_and_max_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"calendars": {"primary": {"busy": []}}},
            headers={"x-echo-min": body["timeMin"], "x-echo-max": body["timeMax"]},
        )

    provider = _provider(handler, minimum_notice_hours=4, max_future_days=7)
    slots = await provider.check_availability(
        window_start=NOW - timedelta(days=1),  # in the past
        window_end=NOW + timedelta(days=30),  # beyond max window
        duration_minutes=60,
    )
    assert slots, "one big free block expected"
    assert slots[0].start >= NOW + timedelta(hours=3, minutes=59)
    assert slots[0].end <= NOW + timedelta(days=7, minutes=1)


async def test_availability_window_collapse_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no API call when the window is empty")

    provider = _provider(handler, minimum_notice_hours=2)
    slots = await provider.check_availability(
        window_start=NOW - timedelta(days=2),
        window_end=NOW + timedelta(hours=1),  # entirely before the notice floor
        duration_minutes=60,
    )
    assert slots == []


async def test_revalidate_slot_true_only_when_free() -> None:
    responses = iter(
        [
            {"calendars": {"primary": {"busy": []}}},
            {
                "calendars": {
                    "primary": {"busy": [{"start": NOW.isoformat(), "end": NOW.isoformat()}]}
                }
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    provider = _provider(handler)
    assert await provider.revalidate_slot(start=NOW, end=NOW + timedelta(hours=1)) is True
    assert await provider.revalidate_slot(start=NOW, end=NOW + timedelta(hours=1)) is False


# --- events ------------------------------------------------------------------


async def test_create_event_carries_identifiers_and_source_marker() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "evt_123"})

    provider = _provider(handler)
    event = await provider.create_event(
        start=NOW,
        end=NOW + timedelta(hours=1),
        summary="Drain cleaning — Pat",
        description="Clogged kitchen sink",
        customer_name="Pat",
        customer_phone="+15550001111",
        address="1 Main St",
        call_id="call-9",
        booking_id="book-7",
    )
    assert event.event_id == "evt_123"
    private = captured["extendedProperties"]["private"]
    assert private["source"] == SOURCE_MARKER
    assert private["call_id"] == "call-9"
    assert private["booking_id"] == "book-7"
    assert captured["location"] == "1 Main St"


# --- error mapping -----------------------------------------------------------


async def test_api_403_is_revocation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    provider = _provider(handler)
    with pytest.raises(CredentialRevokedError):
        await provider.validate_connection()


async def test_api_timeout_maps_to_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    provider = _provider(handler)
    with pytest.raises(ProviderTimeoutError):
        await provider.validate_connection()


async def test_api_500_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = _provider(handler)
    with pytest.raises(ProviderUnavailableError) as excinfo:
        await provider.validate_connection()
    assert excinfo.value.transient is True
