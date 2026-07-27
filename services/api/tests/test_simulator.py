"""Browser simulator tests: session lifecycle, turn persistence,
failure injection, escalation triggers, admin-only access."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from test_config_workflow import valid_config_payload
from tests_markers import requires_db

pytestmark = requires_db


def _admin(mint_token: Callable[..., str]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {mint_token(sub='admin_user', platform_role='platform_admin')}"
    }


@pytest.fixture
async def approved_tenant(client: httpx.AsyncClient, mint_token: Callable[..., str]) -> Any:
    """Tenant with an approved active configuration."""
    headers = _admin(mint_token)
    suffix = uuid.uuid4().hex[:8]
    created = await client.post(
        "/admin/tenants",
        json={
            "business_name": f"Sim Test {suffix}",
            "slug": f"sim-test-{suffix}",
            "timezone": "America/New_York",
            "vertical": "plumbing",
            "primary_owner_email": f"owner-{suffix}@example.com",
            "primary_phone": "+15555550141",
            "escalation_number": "+15555550142",
        },
        headers=headers,
    )
    tenant_id = created.json()["id"]
    base = f"/admin/tenants/{tenant_id}/configuration"
    await client.put(f"{base}/draft", json={"payload": valid_config_payload()}, headers=headers)
    await client.post(f"{base}/draft/submit", headers=headers)
    approved = await client.post(f"{base}/approve", json={"confirm": True}, headers=headers)
    assert approved.status_code == 200, approved.text

    yield tenant_id

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from tests_markers import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL)
    async with AsyncSession(engine) as db, db.begin():
        await db.execute(text("DELETE FROM calls WHERE tenant_id = :tid"), {"tid": tenant_id})
        await db.execute(text("DELETE FROM audit_logs WHERE tenant_id = :tid"), {"tid": tenant_id})
        await db.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
    await engine.dispose()


async def test_session_requires_active_config(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    # seeded tenants have no approved configuration
    response = await client.post(
        f"/admin/tenants/{seeded_tenants['tenant_a_id']}/simulator/sessions",
        headers=_admin(mint_token),
    )
    assert response.status_code == 422
    assert "approved" in response.json()["error"]["message"]


async def test_session_admin_only(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"])
    response = await client.post(
        f"/admin/tenants/{seeded_tenants['tenant_a_id']}/simulator/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


async def test_full_session_flow_persists_turns(
    client: httpx.AsyncClient, mint_token: Callable[..., str], approved_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{approved_tenant}/simulator"

    created = await client.post(f"{base}/sessions", headers=headers)
    assert created.status_code == 201, created.text
    body = created.json()
    call_id = body["call_id"]
    assert "Harbor Plumbing" in body["greeting"]
    # Recording notice plays before the greeting.
    assert body["greeting"].startswith("This call may be recorded")

    turn = await client.post(
        f"{base}/sessions/{call_id}/turns",
        json={"text": "Hi, my kitchen sink is clogged."},
        headers=headers,
    )
    assert turn.status_code == 200, turn.text
    trace = turn.json()
    assert trace["reply_text"]
    assert trace["phase_after"] == "discovery"
    assert trace["total_ms"] is not None

    ended = await client.post(
        f"{base}/sessions/{call_id}/end",
        json={"outcome": "answered_inquiry"},
        headers=headers,
    )
    assert ended.status_code == 200
    assert ended.json()["outcome"] == "answered_inquiry"

    # Turns persisted with the browser_text transport call.
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from tests_markers import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL)
    async with AsyncSession(engine) as db:
        transport = (
            await db.execute(text("SELECT transport FROM calls WHERE id = :cid"), {"cid": call_id})
        ).scalar_one()
        turn_count = (
            await db.execute(
                text("SELECT count(*) FROM turns WHERE call_id = :cid"), {"cid": call_id}
            )
        ).scalar_one()
    await engine.dispose()
    assert transport == "browser_text"
    assert turn_count >= 3  # greeting + caller + reply


async def test_emergency_phrase_escalates(
    client: httpx.AsyncClient, mint_token: Callable[..., str], approved_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{approved_tenant}/simulator"
    call_id = (await client.post(f"{base}/sessions", headers=headers)).json()["call_id"]

    turn = await client.post(
        f"{base}/sessions/{call_id}/turns",
        json={"text": "There's a burst pipe and my basement is flooding!"},
        headers=headers,
    )
    trace = turn.json()
    assert trace["escalation_reason"] == "emergency"
    assert trace["outcome"] == "transferred"
    assert any(g["guardrail_type"] == "emergency" for g in trace["guardrails"])


async def test_human_request_escalates_within_one_turn(
    client: httpx.AsyncClient, mint_token: Callable[..., str], approved_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{approved_tenant}/simulator"
    call_id = (await client.post(f"{base}/sessions", headers=headers)).json()["call_id"]

    turn = await client.post(
        f"{base}/sessions/{call_id}/turns",
        json={"text": "I'd rather talk to a real person please."},
        headers=headers,
    )
    trace = turn.json()
    assert trace["escalation_reason"] == "human_request"
    assert trace["outcome"] == "transferred"


async def test_transfer_failure_flag_falls_back_to_message(
    client: httpx.AsyncClient, mint_token: Callable[..., str], approved_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{approved_tenant}/simulator"
    call_id = (await client.post(f"{base}/sessions", headers=headers)).json()["call_id"]

    flags = await client.put(
        f"{base}/sessions/{call_id}/failures",
        json={"flags": {"transfer_failure": True}},
        headers=headers,
    )
    assert flags.status_code == 200
    assert flags.json()["flags"] == {"transfer_failure": True}

    turn = await client.post(
        f"{base}/sessions/{call_id}/turns",
        json={"text": "Please transfer me to someone."},
        headers=headers,
    )
    trace = turn.json()
    assert trace["escalation_reason"] == "human_request"
    assert trace["outcome"] == "message_taken"
    assert "urgent message" in trace["reply_text"]


async def test_llm_timeout_flag_degrades_to_escalation(
    client: httpx.AsyncClient, mint_token: Callable[..., str], approved_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{approved_tenant}/simulator"
    call_id = (await client.post(f"{base}/sessions", headers=headers)).json()["call_id"]

    await client.put(
        f"{base}/sessions/{call_id}/failures",
        json={"flags": {"llm_timeout": True}},
        headers=headers,
    )
    turn = await client.post(
        f"{base}/sessions/{call_id}/turns",
        json={"text": "What services do you offer?"},
        headers=headers,
    )
    trace = turn.json()
    assert trace["escalation_reason"] == "system_error"
    assert any(g["guardrail_type"] == "system_error" for g in trace["guardrails"])


async def test_unknown_failure_flag_rejected(
    client: httpx.AsyncClient, mint_token: Callable[..., str], approved_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{approved_tenant}/simulator"
    call_id = (await client.post(f"{base}/sessions", headers=headers)).json()["call_id"]

    response = await client.put(
        f"{base}/sessions/{call_id}/failures",
        json={"flags": {"asteroid_strike": True}},
        headers=headers,
    )
    assert response.status_code == 422


async def test_ended_session_rejects_turns(
    client: httpx.AsyncClient, mint_token: Callable[..., str], approved_tenant: str
) -> None:
    headers = _admin(mint_token)
    base = f"/admin/tenants/{approved_tenant}/simulator"
    call_id = (await client.post(f"{base}/sessions", headers=headers)).json()["call_id"]
    await client.post(f"{base}/sessions/{call_id}/end", json={}, headers=headers)

    turn = await client.post(
        f"{base}/sessions/{call_id}/turns", json={"text": "hello?"}, headers=headers
    )
    assert turn.status_code == 422
