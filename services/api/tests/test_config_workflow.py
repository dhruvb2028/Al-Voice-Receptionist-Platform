"""Configuration approval workflow tests."""

import uuid
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


def valid_config_payload() -> dict[str, Any]:
    return {
        "identity": {
            "business_name": "Harbor Plumbing",
            "timezone": "America/New_York",
            "address": "12 Wharf St, Boston, MA",
            "business_phone": "+15555550111",
            "website": "https://harborplumbing.example",
            "emergency_contact": "+15555550122",
        },
        "greeting": {
            "greeting": "Thanks for calling Harbor Plumbing! How can I help you today?",
            "recording_notice": "This call may be recorded for quality purposes.",
            "after_hours_greeting": "You've reached us after hours — I can take a message.",
            "tenant_approved": True,
        },
        "services": [
            {"name": "Drain cleaning", "duration_minutes": 90, "category": "drains"},
            {"name": "Leak repair", "duration_minutes": 120, "category": "leaks"},
        ],
        "prices": [
            {
                "service_name": "Drain cleaning",
                "label": "Standard drain cleaning",
                "minimum_amount_cents": 15000,
                "maximum_amount_cents": 35000,
                "unit": "range",
                "approved": True,
            }
        ],
        "hours": [{"weekday": d, "opens_at": "08:00:00", "closes_at": "18:00:00"} for d in range(5)]
        + [
            {"weekday": 5, "opens_at": "09:00:00", "closes_at": "14:00:00"},
            {"weekday": 6, "closed": True},
        ],
        "holiday_overrides": [{"date": "2026-12-25", "closed": True, "note": "Christmas Day"}],
        "service_area": {"postal_codes": ["02101", "02102"], "cities": ["Boston"]},
        "escalation": {
            "emergency_destination": "+15555550122",
            "transfer_timeout_seconds": 25,
        },
        "voice": {"voice_id": "sonic-warm-1", "max_call_seconds": 900},
    }


@pytest.fixture
async def config_tenant(client: httpx.AsyncClient, mint_token: Callable[..., str]) -> Any:
    """A fresh tenant for workflow tests, cleaned up afterwards."""
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "business_name": f"Config Test {suffix}",
        "slug": f"config-test-{suffix}",
        "timezone": "America/New_York",
        "vertical": "plumbing",
        "primary_owner_email": f"owner-{suffix}@example.com",
        "primary_phone": "+15555550131",
        "escalation_number": "+15555550132",
    }
    created = await client.post("/admin/tenants", json=payload, headers=_admin(mint_token))
    assert created.status_code == 201, created.text
    tenant_id = created.json()["id"]

    yield tenant_id

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from tests_markers import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            text("DELETE FROM audit_logs WHERE tenant_id = :tid"), {"tid": tenant_id}
        )
        await session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
    await engine.dispose()


async def test_invalid_draft_rejected_with_field_errors(
    client: httpx.AsyncClient, mint_token: Callable[..., str], config_tenant: str
) -> None:
    payload = valid_config_payload()
    payload["hours"] = payload["hours"][:5]  # missing weekdays
    payload["prices"][0]["minimum_amount_cents"] = 99999999
    payload["prices"][0]["maximum_amount_cents"] = 1
    response = await client.put(
        f"/admin/tenants/{config_tenant}/configuration/draft",
        json={"payload": payload},
        headers=_admin(mint_token),
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_failed"
    assert body["details"], "field-level errors expected"


async def test_full_workflow_draft_submit_approve(
    client: httpx.AsyncClient, mint_token: Callable[..., str], config_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{config_tenant}/configuration"

    # Draft
    saved = await client.put(
        f"{base}/draft", json={"payload": valid_config_payload()}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["state"] == "draft"

    # Draft is not active: the voice-facing live tables are still empty.
    active_before = await client.get(f"{base}/active", headers=headers)
    assert active_before.json() is None

    # Submit → approve
    submitted = await client.post(f"{base}/draft/submit", headers=headers)
    assert submitted.json()["state"] == "pending_review"

    unconfirmed = await client.post(f"{base}/approve", json={"confirm": False}, headers=headers)
    assert unconfirmed.status_code == 422

    approved = await client.post(f"{base}/approve", json={"confirm": True}, headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "active"

    # Live tables now carry the approved configuration.
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from tests_markers import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL)
    async with AsyncSession(engine) as session:
        greeting = (
            await session.execute(
                text("SELECT greeting FROM tenant_config WHERE tenant_id = :tid"),
                {"tid": config_tenant},
            )
        ).scalar_one()
        service_count = (
            await session.execute(
                text("SELECT count(*) FROM services WHERE tenant_id = :tid"),
                {"tid": config_tenant},
            )
        ).scalar_one()
        hours_count = (
            await session.execute(
                text("SELECT count(*) FROM business_hours WHERE tenant_id = :tid"),
                {"tid": config_tenant},
            )
        ).scalar_one()
    await engine.dispose()
    assert "Harbor Plumbing" in greeting
    assert service_count == 2
    assert hours_count == 7


async def test_second_draft_while_pending_review_conflicts(
    client: httpx.AsyncClient, mint_token: Callable[..., str], config_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{config_tenant}/configuration"
    await client.put(f"{base}/draft", json={"payload": valid_config_payload()}, headers=headers)
    await client.post(f"{base}/draft/submit", headers=headers)

    response = await client.put(
        f"{base}/draft", json={"payload": valid_config_payload()}, headers=headers
    )
    assert response.status_code == 409


async def test_reject_then_new_draft(
    client: httpx.AsyncClient, mint_token: Callable[..., str], config_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{config_tenant}/configuration"
    await client.put(f"{base}/draft", json={"payload": valid_config_payload()}, headers=headers)
    await client.post(f"{base}/draft/submit", headers=headers)

    rejected = await client.post(
        f"{base}/reject",
        json={"notes": "Greeting needs the legal recording line."},
        headers=headers,
    )
    assert rejected.json()["state"] == "rejected"

    # A new draft can be created after rejection.
    saved = await client.put(
        f"{base}/draft", json={"payload": valid_config_payload()}, headers=headers
    )
    assert saved.status_code == 200
    assert saved.json()["version"] == 2


async def test_rollback_restores_previous_configuration(
    client: httpx.AsyncClient, mint_token: Callable[..., str], config_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{config_tenant}/configuration"

    # v1: greeting A
    first = valid_config_payload()
    await client.put(f"{base}/draft", json={"payload": first}, headers=headers)
    await client.post(f"{base}/draft/submit", headers=headers)
    await client.post(f"{base}/approve", json={"confirm": True}, headers=headers)

    # v2: greeting B
    second = valid_config_payload()
    second["greeting"]["greeting"] = "Hello from version two of Harbor Plumbing!"
    await client.put(f"{base}/draft", json={"payload": second}, headers=headers)
    await client.post(f"{base}/draft/submit", headers=headers)
    await client.post(f"{base}/approve", json={"confirm": True}, headers=headers)

    # Roll back to v1 → creates v3 with v1's payload.
    rolled = await client.post(
        f"{base}/rollback", json={"confirm": True, "version": 1}, headers=headers
    )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["state"] == "active"
    assert rolled.json()["version"] == 3

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from tests_markers import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL)
    async with AsyncSession(engine) as session:
        greeting = (
            await session.execute(
                text("SELECT greeting FROM tenant_config WHERE tenant_id = :tid"),
                {"tid": config_tenant},
            )
        ).scalar_one()
    await engine.dispose()
    assert "version two" not in greeting

    versions = await client.get(f"{base}/versions", headers=headers)
    states = {v["version"]: v["state"] for v in versions.json()}
    assert states[1] == "superseded"
    assert states[2] == "superseded"
    assert states[3] == "active"


async def test_client_cannot_touch_config_workflow(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    seeded_tenants: dict[str, Any],
) -> None:
    token = mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"])
    response = await client.put(
        f"/admin/tenants/{seeded_tenants['tenant_a_id']}/configuration/draft",
        json={"payload": valid_config_payload()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


async def test_client_reads_own_configuration(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    seeded_tenants: dict[str, Any],
) -> None:
    token = mint_token(sub=seeded_tenants["staff_a"], org_id=seeded_tenants["org_a"])
    response = await client.get(
        "/tenant/configuration", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert "services" in body
    assert "hours" in body
