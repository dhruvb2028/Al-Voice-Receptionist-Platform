"""Overview metrics: real counts, honest nulls, after-hours detection,
latency percentiles, the no-fabricated-revenue rule, and the platform
console view."""

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def seeded_activity(
    migrated_database: str, seeded_tenants: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """A week of activity for tenant A: four calls with known outcomes,
    a confirmed booking, two messages, turn latencies, and usage."""
    from ai_database.enums import (
        BookingStatus,
        CallOutcome,
        DeliveryStatus,
        TranscriptStatus,
        TurnRole,
        Urgency,
    )
    from ai_database.models import (
        Booking,
        BusinessHours,
        Call,
        Message,
        TenantConfig,
        Turn,
        UsageRecord,
    )

    engine = create_async_engine(migrated_database)
    tenant_id = seeded_tenants["tenant_a_id"]
    suffix = seeded_tenants["suffix"]
    # The most recent Wednesday at midday: a fixed weekday keeps the
    # after-hours assertions stable, and staying near "now" keeps the
    # rows inside the default 30-day window the live endpoint uses.
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    base = today - timedelta(days=(today.weekday() - 2) % 7)
    data: dict[str, Any] = {"tenant_id": tenant_id, "base": base}

    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        session.add(
            TenantConfig(
                tenant_id=tenant_id,
                language="en",
                recording_enabled=False,
                max_call_seconds=900,
                configuration_version=1,
                timezone="UTC",
            )
        )
        # Open 09:00–17:00 every weekday, closed on the weekend.
        for weekday in range(7):
            session.add(
                BusinessHours(
                    tenant_id=tenant_id,
                    weekday=weekday,
                    opens_at=None if weekday >= 5 else time(9, 0),
                    closes_at=None if weekday >= 5 else time(17, 0),
                    closed=weekday >= 5,
                )
            )

        # Wednesday base; -1 and -2 days stay on weekdays inside 09:00–17:00.
        specs = [
            ("booked", CallOutcome.BOOKED, 240, base - timedelta(days=1)),
            ("message", CallOutcome.MESSAGE_TAKEN, 120, base - timedelta(days=2)),
            # 21:00 on a Tuesday — the only call outside 09:00–17:00.
            (
                "after_hours",
                CallOutcome.ANSWERED_INQUIRY,
                90,
                (base - timedelta(days=1)).replace(hour=21),
            ),
            ("transferred", CallOutcome.TRANSFERRED, 60, base - timedelta(hours=1)),
            ("failed", CallOutcome.FAILED, 15, base - timedelta(hours=2)),
        ]
        calls: dict[str, Call] = {}
        for name, outcome, duration, started in specs:
            call = Call(
                tenant_id=tenant_id,
                provider_call_sid=f"CA_m_{name}_{suffix}",
                to_number="+15550004000",
                started_at=started,
                ended_at=started + timedelta(seconds=duration),
                duration_seconds=duration,
                outcome=outcome,
                transcript_status=TranscriptStatus.COMPLETE,
                estimated_cost_cents=10,
            )
            calls[name] = call
            session.add(call)
        await session.flush()

        # Latencies: 100, 200, 300, 400, 500 ms → p50 = 300.
        for index, latency in enumerate([100, 200, 300, 400, 500]):
            session.add(
                Turn(
                    tenant_id=tenant_id,
                    call_id=calls["booked"].id,
                    turn_index=index,
                    role=TurnRole.ASSISTANT,
                    text=f"turn {index}",
                    total_latency_ms=latency,
                    created_at=base - timedelta(days=1),
                )
            )

        # Timestamps are explicit: the metrics window is bounded at both
        # ends, so rows must be dated inside it rather than at wall-clock
        # "now".
        session.add_all(
            [
                Booking(
                    tenant_id=tenant_id,
                    call_id=calls["booked"].id,
                    customer_name="Metric Customer",
                    scheduled_at=base + timedelta(days=2),
                    timezone="UTC",
                    idempotency_key=f"bk_m_{suffix}",
                    status=BookingStatus.CONFIRMED,
                    created_at=base - timedelta(days=1),
                    updated_at=base - timedelta(days=1),
                ),
                Message(
                    tenant_id=tenant_id,
                    call_id=calls["message"].id,
                    body_encrypted="enc:one",
                    urgency=Urgency.EMERGENCY,
                    delivery_status=DeliveryStatus.SENT,
                    created_at=base - timedelta(days=2),
                ),
                Message(
                    tenant_id=tenant_id,
                    call_id=calls["message"].id,
                    body_encrypted="enc:two",
                    urgency=Urgency.ROUTINE,
                    delivery_status=DeliveryStatus.PENDING,
                    created_at=base - timedelta(days=2),
                ),
                UsageRecord(
                    tenant_id=tenant_id,
                    call_id=calls["booked"].id,
                    provider="twilio",
                    usage_type="call_minutes",
                    quantity=9,
                    unit="minutes",
                    recorded_at=base - timedelta(days=1),
                ),
            ]
        )
        data["call_ids"] = {name: call.id for name, call in calls.items()}

    try:
        yield data
    finally:
        async with AsyncSession(engine) as session, session.begin():
            for table in ["turns", "usage_records", "messages", "bookings"]:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id = :tid"),  # noqa: S608
                    {"tid": tenant_id},
                )
            await session.execute(
                text("DELETE FROM calls WHERE provider_call_sid LIKE :pat"),
                {"pat": f"CA_m_%_{suffix}"},
            )
            await session.execute(
                text("DELETE FROM business_hours WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
            await session.execute(
                text("DELETE FROM tenant_config WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
        await engine.dispose()


@pytest.fixture
def owner_headers(mint_token: Callable[..., str], seeded_tenants: dict[str, Any]) -> dict[str, str]:
    return _auth(mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"]))


@pytest.fixture
def admin_headers(mint_token: Callable[..., str]) -> dict[str, str]:
    return _auth(mint_token(sub="admin_user", platform_role="platform_admin"))


async def _overview(db_url: str, tenant_id: uuid.UUID, base: datetime) -> Any:
    """Call the service directly with a fixed clock."""
    from api.services.metrics import tenant_overview

    engine = create_async_engine(db_url)
    async with AsyncSession(engine) as session:
        result = await tenant_overview(session, tenant_id, window_days=30, now=base)
    await engine.dispose()
    return result


async def test_headline_counts_are_real(
    migrated_database: str, seeded_activity: dict[str, Any]
) -> None:
    overview = await _overview(
        migrated_database, seeded_activity["tenant_id"], seeded_activity["base"]
    )
    m = overview.metrics
    assert m.calls_answered == 5
    assert m.appointments_booked == 1
    assert m.messages_captured == 2
    assert m.calls_transferred == 1
    assert m.calls_failed == 1
    # booked + message_taken + answered_inquiry = 3 of 5
    assert m.containment_rate == pytest.approx(3 / 5)
    assert m.average_call_seconds == pytest.approx((240 + 120 + 90 + 60 + 15) / 5)


async def test_after_hours_uses_business_hours(
    migrated_database: str, seeded_activity: dict[str, Any]
) -> None:
    overview = await _overview(
        migrated_database, seeded_activity["tenant_id"], seeded_activity["base"]
    )
    # Only the 21:00 call falls outside the 09:00–17:00 weekday window.
    assert overview.metrics.calls_after_hours == 1


async def test_latency_percentiles(migrated_database: str, seeded_activity: dict[str, Any]) -> None:
    overview = await _overview(
        migrated_database, seeded_activity["tenant_id"], seeded_activity["base"]
    )
    assert overview.metrics.latency_p50_ms == pytest.approx(300.0)
    assert overview.metrics.latency_p95_ms == pytest.approx(480.0)


async def test_metrics_without_data_are_null_not_zero(
    migrated_database: str, seeded_tenants: dict[str, Any]
) -> None:
    """Tenant B has one call with no outcome, no turns: containment and
    latency are unmeasured, which must not read as 0%."""
    overview = await _overview(migrated_database, seeded_tenants["tenant_b_id"], datetime.now(UTC))
    assert overview.metrics.latency_p50_ms is None
    assert overview.metrics.latency_p95_ms is None
    assert overview.metrics.containment_rate == 0.0  # calls exist, none contained

    # A tenant with no calls at all has an undefined containment rate.
    empty = await _overview(migrated_database, seeded_tenants["suspended_id"], datetime.now(UTC))
    assert empty.metrics.calls_answered == 0
    assert empty.metrics.containment_rate is None


async def test_recovered_revenue_absent_without_tenant_input(
    migrated_database: str, seeded_activity: dict[str, Any]
) -> None:
    overview = await _overview(
        migrated_database, seeded_activity["tenant_id"], seeded_activity["base"]
    )
    # No average job value configured — the platform invents nothing.
    assert overview.metrics.recovered_revenue is None


async def test_recovered_revenue_is_labelled_estimate_when_supplied(
    migrated_database: str, seeded_activity: dict[str, Any]
) -> None:
    engine = create_async_engine(migrated_database)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            text("UPDATE tenant_config SET average_job_value_cents = 45000 WHERE tenant_id = :tid"),
            {"tid": seeded_activity["tenant_id"]},
        )
    await engine.dispose()

    overview = await _overview(
        migrated_database, seeded_activity["tenant_id"], seeded_activity["base"]
    )
    revenue = overview.metrics.recovered_revenue
    assert revenue is not None
    assert revenue.is_estimate is True
    assert revenue.average_job_value_cents == 45000
    assert revenue.bookings_counted == 1
    assert revenue.amount_cents == 45000


async def test_series_are_dense_and_ordered(
    migrated_database: str, seeded_activity: dict[str, Any]
) -> None:
    overview = await _overview(
        migrated_database, seeded_activity["tenant_id"], seeded_activity["base"]
    )
    series = overview.series
    # Daily series covers the whole window with no gaps.
    labels = [p.label for p in series.calls_over_time]
    assert labels == sorted(labels)
    assert len(labels) == 31
    assert sum(p.value for p in series.calls_over_time) == 5

    assert len(series.calls_by_hour) == 24
    assert next(p.value for p in series.calls_by_hour if p.label == "21") == 1

    outcomes = {p.label: p.value for p in series.outcomes}
    assert outcomes["booked"] == 1
    assert outcomes["failed"] == 1

    urgencies = {p.label: p.value for p in series.urgency_distribution}
    assert urgencies == {"emergency": 1, "routine": 1}

    assert sum(p.value for p in series.bookings_over_time) == 1
    assert [p.value for p in series.latency_trend] == [pytest.approx(300.0)]
    assert [p.value for p in series.usage_trend] == [pytest.approx(9.0)]


async def test_overview_endpoint_requires_tenant_scope(
    client: httpx.AsyncClient,
    seeded_activity: dict[str, Any],
    owner_headers: dict[str, str],
) -> None:
    response = await client.get("/tenant/overview", headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["metrics"]["calls_answered"] >= 5
    assert "calls_over_time" in body["series"]

    anonymous = await client.get("/tenant/overview")
    assert anonymous.status_code == 401


async def test_usage_endpoint_is_owner_only(
    client: httpx.AsyncClient,
    seeded_activity: dict[str, Any],
    seeded_tenants: dict[str, Any],
    mint_token: Callable[..., str],
    owner_headers: dict[str, str],
) -> None:
    assert (await client.get("/tenant/usage", headers=owner_headers)).status_code == 200

    staff = _auth(mint_token(sub=seeded_tenants["staff_a"], org_id=seeded_tenants["org_a"]))
    response = await client.get("/tenant/usage", headers=staff)
    assert response.status_code in (403, 404)


async def test_platform_overview(
    client: httpx.AsyncClient, seeded_activity: dict[str, Any], admin_headers: dict[str, str]
) -> None:
    response = await client.get("/admin/overview", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["active_tenants"] >= 2
    assert isinstance(body["tenants_by_status"], list)
    assert body["calendar_connection_failures"] >= 0
    assert isinstance(body["readiness_warnings"], list)
    assert isinstance(body["provider_errors_today"], list)


async def test_platform_overview_is_admin_only(
    client: httpx.AsyncClient, seeded_activity: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get("/admin/overview", headers=owner_headers)
    assert response.status_code == 404


async def test_admin_tenant_overview(
    client: httpx.AsyncClient, seeded_activity: dict[str, Any], admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"/admin/tenants/{seeded_activity['tenant_id']}/overview", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["metrics"]["appointments_booked"] == 1

    missing = await client.get(f"/admin/tenants/{uuid.uuid4()}/overview", headers=admin_headers)
    assert missing.status_code == 404
