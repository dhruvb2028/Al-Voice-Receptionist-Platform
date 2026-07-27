"""Onboarding workflow: derived vs attested steps, waivers, audit, and
the three generated reports."""

import uuid
from collections.abc import AsyncIterator, Callable
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
async def onboarding_tenant(migrated_database: str) -> AsyncIterator[dict[str, Any]]:
    """A tenant mid-onboarding: some rows exist, nothing attested."""
    engine = create_async_engine(migrated_database)
    tenant_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, slug, vertical, timezone, status) "
                "VALUES (:tid, 'Onboard Plumbing', :slug, 'plumbing', 'UTC', 'onboarding')"
            ),
            {"tid": tenant_id, "slug": f"onb-{suffix}"},
        )
        await session.execute(
            text(
                "INSERT INTO tenant_config (tenant_id, language, recording_enabled, "
                "max_call_seconds, configuration_version, greeting, escalation_number, "
                "voice_id) "
                "VALUES (:tid, 'en', false, 900, 1, 'Thanks for calling Onboard Plumbing.', "
                "'+15550009000', 'voice_abc')"
            ),
            {"tid": tenant_id},
        )
        await session.execute(
            text(
                "INSERT INTO services (id, tenant_id, name, name_normalized, "
                "duration_minutes, active) "
                "VALUES (:id, :tid, 'Drain Cleaning', :norm, 60, true)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "norm": f"drain cleaning {suffix}"},
        )
        for weekday in range(5):
            await session.execute(
                text(
                    "INSERT INTO business_hours (id, tenant_id, weekday, opens_at, "
                    "closes_at, closed) VALUES (:id, :tid, :wd, '09:00', '17:00', false)"
                ),
                {"id": uuid.uuid4(), "tid": tenant_id, "wd": weekday},
            )
    try:
        yield {"tenant_id": tenant_id, "suffix": suffix}
    finally:
        async with AsyncSession(engine) as session, session.begin():
            for table in ("business_hours", "services", "audit_logs", "tenant_config"):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id = :tid"),  # noqa: S608
                    {"tid": tenant_id},
                )
            await session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
        await engine.dispose()


async def _state(db_url: str, tenant_id: uuid.UUID) -> Any:
    from api.services.onboarding import onboarding_state

    engine = create_async_engine(db_url)
    async with AsyncSession(engine) as session:
        result = await onboarding_state(session, tenant_id)
    await engine.dispose()
    return result


def _step(state: Any, key: str) -> Any:
    return next(s for s in state.steps if s.key == key)


# --- derived steps -----------------------------------------------------------


async def test_every_declared_step_appears(
    migrated_database: str, onboarding_tenant: dict[str, Any]
) -> None:
    from api.services.onboarding import STEPS

    state = await _state(migrated_database, onboarding_tenant["tenant_id"])
    assert [s.key for s in state.steps] == [spec.key for spec in STEPS]
    assert state.total_steps == len(STEPS)


async def test_derived_steps_reflect_real_rows(
    migrated_database: str, onboarding_tenant: dict[str, Any]
) -> None:
    """A step is complete because the data exists, not because someone
    ticked a box."""
    state = await _state(migrated_database, onboarding_tenant["tenant_id"])
    assert _step(state, "services").status.value == "complete"
    assert _step(state, "business_hours").status.value == "complete"
    assert _step(state, "voice").status.value == "complete"
    # Nothing assigned or connected yet.
    assert _step(state, "phone_number").status.value == "blocked"
    assert _step(state, "calendar").status.value == "blocked"


async def test_recording_notice_is_satisfied_when_recording_is_off(
    migrated_database: str, onboarding_tenant: dict[str, Any]
) -> None:
    state = await _state(migrated_database, onboarding_tenant["tenant_id"])
    step = _step(state, "recording_notice")
    assert step.status.value == "complete"
    assert "off" in step.detail.lower()


async def test_recording_on_without_a_notice_blocks(
    migrated_database: str, onboarding_tenant: dict[str, Any]
) -> None:
    engine = create_async_engine(migrated_database)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            text("UPDATE tenant_config SET recording_enabled = true WHERE tenant_id = :tid"),
            {"tid": onboarding_tenant["tenant_id"]},
        )
    await engine.dispose()

    state = await _state(migrated_database, onboarding_tenant["tenant_id"])
    assert _step(state, "recording_notice").status.value == "blocked"


async def test_attested_steps_start_pending(
    migrated_database: str, onboarding_tenant: dict[str, Any]
) -> None:
    state = await _state(migrated_database, onboarding_tenant["tenant_id"])
    for key in ("greeting", "browser_text_test", "real_phone_test", "safety_review"):
        assert _step(state, key).status.value == "pending"


# --- attestation -------------------------------------------------------------


async def test_recording_a_step_completes_it_and_names_the_actor(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    tenant_id = onboarding_tenant["tenant_id"]
    response = await client.post(
        f"/admin/tenants/{tenant_id}/onboarding/greeting/record",
        json={"passed": True, "note": "Owner approved on the call."},
        headers=admin_headers,
    )
    assert response.status_code == 200
    step = next(s for s in response.json()["steps"] if s["key"] == "greeting")
    assert step["status"] == "complete"
    assert step["attested_by"] == "admin_user"
    assert step["attested_at"]


async def test_recording_can_be_undone(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    tenant_id = onboarding_tenant["tenant_id"]
    url = f"/admin/tenants/{tenant_id}/onboarding/browser_text_test/record"
    await client.post(url, json={"passed": True}, headers=admin_headers)
    response = await client.post(url, json={"passed": False}, headers=admin_headers)
    step = next(s for s in response.json()["steps"] if s["key"] == "browser_text_test")
    assert step["status"] == "pending"


async def test_derived_step_cannot_be_ticked_by_hand(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    """The whole point of a derived step is that it cannot lie."""
    response = await client.post(
        f"/admin/tenants/{onboarding_tenant['tenant_id']}/onboarding/services/record",
        json={"passed": True},
        headers=admin_headers,
    )
    assert response.status_code == 422


async def test_unknown_step_is_rejected(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    response = await client.post(
        f"/admin/tenants/{onboarding_tenant['tenant_id']}/onboarding/not_a_step/record",
        json={"passed": True},
        headers=admin_headers,
    )
    assert response.status_code == 422


async def test_attestation_is_audited(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
    migrated_database: str,
) -> None:
    tenant_id = onboarding_tenant["tenant_id"]
    await client.post(
        f"/admin/tenants/{tenant_id}/onboarding/safety_review/record",
        json={"passed": True},
        headers=admin_headers,
    )
    engine = create_async_engine(migrated_database)
    async with AsyncSession(engine) as session:
        row = (
            await session.execute(
                text(
                    "SELECT actor_external_user_id FROM audit_logs "
                    "WHERE action = 'onboarding.step_recorded' AND tenant_id = :tid"
                ),
                {"tid": tenant_id},
            )
        ).scalar_one()
    await engine.dispose()
    assert row == "admin_user"


# --- waivers -----------------------------------------------------------------


async def test_waiving_requires_a_reason(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    """A waiver without a justification is just a skipped check."""
    response = await client.post(
        f"/admin/tenants/{onboarding_tenant['tenant_id']}/onboarding/real_phone_test/waive",
        json={"reason": "nah"},
        headers=admin_headers,
    )
    assert response.status_code == 422


async def test_waiving_records_who_and_why(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    response = await client.post(
        f"/admin/tenants/{onboarding_tenant['tenant_id']}/onboarding/real_phone_test/waive",
        json={"reason": "Number ports on Monday; client accepted the risk in writing."},
        headers=admin_headers,
    )
    assert response.status_code == 200
    step = next(s for s in response.json()["steps"] if s["key"] == "real_phone_test")
    assert step["status"] == "complete"
    assert step["waived"] is True
    assert "ports on Monday" in step["waiver_reason"]


async def test_non_waivable_step_cannot_be_waived(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    response = await client.post(
        f"/admin/tenants/{onboarding_tenant['tenant_id']}/onboarding/safety_review/waive",
        json={"reason": "We are in a hurry and would rather not check."},
        headers=admin_headers,
    )
    assert response.status_code == 422


# --- readiness ---------------------------------------------------------------


async def test_incomplete_onboarding_is_not_ready(
    migrated_database: str, onboarding_tenant: dict[str, Any]
) -> None:
    state = await _state(migrated_database, onboarding_tenant["tenant_id"])
    assert state.readiness.ready is False
    assert state.readiness.blockers


async def test_progress_is_counted(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    tenant_id = onboarding_tenant["tenant_id"]
    before = (
        await client.get(f"/admin/tenants/{tenant_id}/onboarding", headers=admin_headers)
    ).json()["completed_steps"]
    after = (
        await client.post(
            f"/admin/tenants/{tenant_id}/onboarding/greeting/record",
            json={"passed": True},
            headers=admin_headers,
        )
    ).json()["completed_steps"]
    assert after == before + 1


# --- reports -----------------------------------------------------------------


async def test_handover_checklist_states_the_limits(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    response = await client.get(
        f"/admin/tenants/{onboarding_tenant['tenant_id']}/onboarding/reports/handover",
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Client handover checklist"
    headings = [s["heading"] for s in body["sections"]]
    assert "What it will not do" in headings
    limits = next(s for s in body["sections"] if s["heading"] == "What it will not do")
    assert any("price you have not approved" in item for item in limits["items"])


async def test_test_call_report_lists_every_test(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    tenant_id = onboarding_tenant["tenant_id"]
    await client.post(
        f"/admin/tenants/{tenant_id}/onboarding/browser_text_test/record",
        json={"passed": True, "note": "Booked, declined out-of-area, escalated gas smell."},
        headers=admin_headers,
    )
    response = await client.get(
        f"/admin/tenants/{tenant_id}/onboarding/reports/test-calls", headers=admin_headers
    )
    tests = response.json()["sections"][0]["items"]
    by_name = {t["test"]: t for t in tests}
    assert by_name["Browser text test"]["result"] == "passed"
    assert by_name["Browser text test"]["by"] == "admin_user"
    assert by_name["Real phone test"]["result"] == "not run"


async def test_activation_report_lists_outstanding_blockers(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    response = await client.get(
        f"/admin/tenants/{onboarding_tenant['tenant_id']}/onboarding/reports/activation",
        headers=admin_headers,
    )
    body = response.json()
    assert body["title"] == "Activation report"
    blockers = next(s for s in body["sections"] if s["heading"] == "Outstanding blockers")
    assert blockers["items"] and blockers["items"] != ["None"]


async def test_unknown_report_is_404(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    response = await client.get(
        f"/admin/tenants/{onboarding_tenant['tenant_id']}/onboarding/reports/nope",
        headers=admin_headers,
    )
    assert response.status_code == 404


# --- access ------------------------------------------------------------------


async def test_onboarding_is_admin_only(
    client: httpx.AsyncClient,
    onboarding_tenant: dict[str, Any],
    seeded_tenants: dict[str, Any],
    mint_token: Callable[..., str],
) -> None:
    owner = _auth(mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"]))
    response = await client.get(
        f"/admin/tenants/{onboarding_tenant['tenant_id']}/onboarding", headers=owner
    )
    assert response.status_code == 404


async def test_unknown_tenant_is_404(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.get(f"/admin/tenants/{uuid.uuid4()}/onboarding", headers=admin_headers)
    assert response.status_code == 404


def test_onboarding_needs_no_code_change_per_tenant() -> None:
    """Steps are data. Onboarding a second tenant is configuration."""
    from api.services.onboarding import STEPS

    assert all(isinstance(step.key, str) for step in STEPS)
    assert len({step.key for step in STEPS}) == len(STEPS)
