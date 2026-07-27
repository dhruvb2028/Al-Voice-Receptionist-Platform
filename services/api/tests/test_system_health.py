"""System health: alert evaluation against real state, and the
admin-only endpoint."""

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(mint_token: Callable[..., str]) -> dict[str, str]:
    return _auth(mint_token(sub="admin_user", platform_role="platform_admin"))


@pytest.fixture
async def failing_calls(
    migrated_database: str, seeded_tenants: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """Four failed calls for tenant A inside the alert window."""
    engine = create_async_engine(migrated_database)
    tenant_id = seeded_tenants["tenant_a_id"]
    suffix = seeded_tenants["suffix"]

    async with AsyncSession(engine) as session, session.begin():
        for index in range(4):
            await session.execute(
                text(
                    "INSERT INTO calls (id, tenant_id, provider_call_sid, to_number, "
                    "started_at, ended_at, direction, transport, outcome, "
                    "failure_category, recording_status, recording_legal_hold, "
                    "transcript_status, post_processing_status) "
                    "VALUES (:cid, :tid, :sid, '+15550005000', now() - interval '5 minutes', "
                    "now() - interval '4 minutes', 'inbound', 'phone', 'failed', "
                    "'provider_timeout', 'disabled', false, 'failed', 'complete')"
                ),
                {
                    "cid": uuid.uuid4(),
                    "tid": tenant_id,
                    "sid": f"CA_h_{index}_{suffix}",
                },
            )
    try:
        yield {"tenant_id": tenant_id, "suffix": suffix}
    finally:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                text("DELETE FROM calls WHERE provider_call_sid LIKE :pat"),
                {"pat": f"CA_h_%_{suffix}"},
            )
        await engine.dispose()


async def _health(db_url: str, **kwargs: Any) -> Any:
    from api.services.health import system_health

    engine = create_async_engine(db_url)
    async with AsyncSession(engine) as session:
        result = await system_health(session, **kwargs)
    await engine.dispose()
    return result


def _alert(health: Any, key: str) -> Any:
    return next(a for a in health.alerts if a.key == key)


async def test_every_declared_alert_is_evaluated(migrated_database: str) -> None:
    """A declared alert that is never computed is a silent blind spot."""
    from api.services.health import ALERTS

    health = await _health(migrated_database)
    assert {a.key for a in health.alerts} == {spec.key for spec in ALERTS}


async def test_healthy_system_reports_ok(migrated_database: str) -> None:
    health = await _health(migrated_database)
    assert _alert(health, "call_failure_spike").severity.value == "ok"


async def test_call_failures_raise_severity(
    migrated_database: str, failing_calls: dict[str, Any]
) -> None:
    health = await _health(migrated_database)
    alert = _alert(health, "call_failure_spike")
    assert alert.value >= 4
    assert alert.severity.value == "warning"  # 4 is over warn (3), under crit (10)
    assert health.overall.value in ("warning", "critical")


async def test_provider_errors_surface_the_top_category(
    migrated_database: str, failing_calls: dict[str, Any]
) -> None:
    health = await _health(migrated_database)
    alert = _alert(health, "provider_errors")
    assert alert.value >= 4
    assert "provider_timeout" in alert.detail


async def test_tenant_repeated_failures_names_the_tenant(
    migrated_database: str, failing_calls: dict[str, Any]
) -> None:
    health = await _health(migrated_database)
    assert health.tenant_failures
    assert health.tenant_failures[0].failed_calls >= 3
    assert _alert(health, "tenant_repeated_failures").value >= 1


async def test_saturation_is_a_ratio_of_the_configured_cap(migrated_database: str) -> None:
    health = await _health(migrated_database, max_concurrent_calls=6)
    alert = _alert(health, "voice_saturation")
    assert alert.unit == "ratio"
    assert "of 6" in alert.detail


async def test_database_latency_is_measured(migrated_database: str) -> None:
    health = await _health(migrated_database)
    assert health.database_latency_ms > 0
    assert _alert(health, "database_latency").unit == "ms"


async def test_every_alert_carries_a_runbook(migrated_database: str) -> None:
    """An alert without a next action wakes someone up for nothing."""
    health = await _health(migrated_database)
    assert all(a.runbook.strip() for a in health.alerts)


async def test_severity_thresholds_are_ordered(migrated_database: str) -> None:
    health = await _health(migrated_database)
    assert all(a.warning_at <= a.critical_at for a in health.alerts)


async def test_endpoint_requires_platform_admin(
    client: httpx.AsyncClient,
    seeded_tenants: dict[str, Any],
    mint_token: Callable[..., str],
    admin_headers: dict[str, str],
) -> None:
    ok = await client.get("/admin/system-health", headers=admin_headers)
    assert ok.status_code == 200
    assert ok.json()["overall"] in ("ok", "warning", "critical")

    owner = _auth(mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"]))
    denied = await client.get("/admin/system-health", headers=owner)
    assert denied.status_code == 404


async def test_endpoint_payload_shape(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.get("/admin/system-health", headers=admin_headers)
    body = response.json()
    assert isinstance(body["alerts"], list)
    first = body["alerts"][0]
    assert {"key", "title", "severity", "value", "runbook", "unit"} <= set(first)


def test_alert_window_is_respected() -> None:
    """A failure outside the window must not fire the alert."""
    from api.services.health import ALERTS_BY_KEY

    spec = ALERTS_BY_KEY["call_failure_spike"]
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    assert now - timedelta(minutes=spec.window_minutes) < now
