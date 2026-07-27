"""Admin tenant-management tests: creation, readiness blockers,
activation gating, lifecycle confirmation, list stats."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from tests_markers import requires_db

pytestmark = requires_db


def _admin(mint_token: Callable[..., str]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {mint_token(sub='admin_user', platform_role='platform_admin')}"
    }


@pytest.fixture
def create_payload() -> dict[str, Any]:
    import uuid

    suffix = uuid.uuid4().hex[:8]
    return {
        "business_name": f"Test HVAC {suffix}",
        "slug": f"test-hvac-{suffix}",
        "timezone": "America/Chicago",
        "vertical": "hvac",
        "primary_owner_email": f"owner-{suffix}@example.com",
        "primary_phone": "+15555550111",
        "escalation_number": "+15555550122",
        "country": "US",
        "expected_monthly_calls": 300,
    }


async def _cleanup(slug: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from tests_markers import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL)
    async with AsyncSession(engine) as session, session.begin():
        # Audit rows intentionally RESTRICT tenant deletion; tests remove
        # them explicitly before removing the tenant.
        await session.execute(
            text(
                "DELETE FROM audit_logs WHERE tenant_id = "
                "(SELECT id FROM tenants WHERE slug = :slug)"
            ),
            {"slug": slug},
        )
        await session.execute(text("DELETE FROM tenants WHERE slug = :slug"), {"slug": slug})
    await engine.dispose()


async def test_create_tenant_full_flow(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    create_payload: dict[str, Any],
) -> None:
    try:
        response = await client.post(
            "/admin/tenants", json=create_payload, headers=_admin(mint_token)
        )
        assert response.status_code == 201, response.text
        body = response.json()
        # Never activated automatically.
        assert body["status"] == "onboarding"
        # Auth organization mapped (Null provider in tests).
        assert body["external_auth_org_id"] == f"org_local_{create_payload['slug']}"
        assert body["owner_invited"] is True

        # Audit event exists.
        from sqlalchemy import text as sql_text
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from tests_markers import TEST_DATABASE_URL

        engine = create_async_engine(TEST_DATABASE_URL)
        async with AsyncSession(engine) as session:
            count = (
                await session.execute(
                    sql_text(
                        "SELECT count(*) FROM audit_logs WHERE tenant_id = :tid "
                        "AND action = 'tenant.created'"
                    ),
                    {"tid": body["id"]},
                )
            ).scalar_one()
        await engine.dispose()
        assert count == 1
    finally:
        await _cleanup(create_payload["slug"])


async def test_create_tenant_duplicate_slug_conflicts(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    create_payload: dict[str, Any],
) -> None:
    try:
        first = await client.post("/admin/tenants", json=create_payload, headers=_admin(mint_token))
        assert first.status_code == 201
        second = await client.post(
            "/admin/tenants", json=create_payload, headers=_admin(mint_token)
        )
        assert second.status_code == 409
    finally:
        await _cleanup(create_payload["slug"])


async def test_create_tenant_validation(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    create_payload: dict[str, Any],
) -> None:
    bad = dict(create_payload)
    bad["escalation_number"] = "555-not-e164"
    response = await client.post("/admin/tenants", json=bad, headers=_admin(mint_token))
    assert response.status_code == 422
    fields = {d.get("field") for d in response.json()["error"]["details"]}
    assert any("escalation_number" in (f or "") for f in fields)


async def test_new_tenant_has_full_blocker_list(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    create_payload: dict[str, Any],
) -> None:
    try:
        created = await client.post(
            "/admin/tenants", json=create_payload, headers=_admin(mint_token)
        )
        tenant_id = created.json()["id"]
        response = await client.get(
            f"/admin/tenants/{tenant_id}/activation-readiness", headers=_admin(mint_token)
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is False
        codes = {b["code"] for b in body["blockers"]}
        assert {
            "greeting_missing",
            "no_services",
            "no_business_hours",
            "escalation_unverified",
            "no_phone_number",
            "calendar_unhealthy",
            "browser_test_missing",
            "phone_test_missing",
            "safety_config_unapproved",
        } <= codes
        waivable = {b["code"] for b in body["blockers"] if b["waivable"]}
        assert waivable == {"phone_test_missing"}
    finally:
        await _cleanup(create_payload["slug"])


async def test_activation_blocked_until_ready(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    create_payload: dict[str, Any],
) -> None:
    try:
        created = await client.post(
            "/admin/tenants", json=create_payload, headers=_admin(mint_token)
        )
        tenant_id = created.json()["id"]
        # onboarding -> testing is allowed…
        moved = await client.post(
            f"/admin/tenants/{tenant_id}/begin-testing",
            json={"confirm": True},
            headers=_admin(mint_token),
        )
        assert moved.status_code == 200
        # …but testing -> active is blocked by readiness.
        response = await client.post(
            f"/admin/tenants/{tenant_id}/activate",
            json={"confirm": True},
            headers=_admin(mint_token),
        )
        assert response.status_code == 422
        assert "not ready" in response.json()["error"]["message"]
    finally:
        await _cleanup(create_payload["slug"])


async def test_lifecycle_requires_confirmation(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    create_payload: dict[str, Any],
) -> None:
    try:
        created = await client.post(
            "/admin/tenants", json=create_payload, headers=_admin(mint_token)
        )
        tenant_id = created.json()["id"]
        response = await client.post(
            f"/admin/tenants/{tenant_id}/begin-testing",
            json={"confirm": False},
            headers=_admin(mint_token),
        )
        assert response.status_code == 422
        assert "confirmation" in response.json()["error"]["message"]
    finally:
        await _cleanup(create_payload["slug"])


async def test_invalid_transition_rejected(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    create_payload: dict[str, Any],
) -> None:
    try:
        created = await client.post(
            "/admin/tenants", json=create_payload, headers=_admin(mint_token)
        )
        tenant_id = created.json()["id"]
        # onboarding -> paused is not a legal transition.
        response = await client.post(
            f"/admin/tenants/{tenant_id}/pause",
            json={"confirm": True},
            headers=_admin(mint_token),
        )
        assert response.status_code == 422
    finally:
        await _cleanup(create_payload["slug"])


async def test_tenant_list_includes_stats_and_filters(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    create_payload: dict[str, Any],
) -> None:
    try:
        await client.post("/admin/tenants", json=create_payload, headers=_admin(mint_token))
        response = await client.get(
            "/admin/tenants",
            params={"search": create_payload["slug"], "status": "onboarding"},
            headers=_admin(mint_token),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["slug"] == create_payload["slug"]
        for key in (
            "assigned_numbers",
            "calls_today",
            "calls_this_month",
            "failed_calls_this_month",
            "calendar_health",
            "usage_minutes_this_month",
            "configuration_ready",
        ):
            assert key in item
        assert item["configuration_ready"] is False
        assert item["calendar_health"] == "not_connected"
    finally:
        await _cleanup(create_payload["slug"])


async def test_admin_endpoints_hidden_from_clients(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    seeded_tenants: dict[str, Any],
    create_payload: dict[str, Any],
) -> None:
    token = mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"])
    response = await client.post(
        "/admin/tenants", json=create_payload, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404
