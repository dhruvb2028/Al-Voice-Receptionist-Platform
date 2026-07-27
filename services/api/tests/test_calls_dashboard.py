"""Calls dashboard API: list filters/sort/pagination/search, CSV export,
client detail (technical fields hidden), and admin-expanded detail."""

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
async def seeded_calls(
    migrated_database: str, seeded_tenants: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """Three calls in tenant A with related records for every surface."""
    from ai_database.enums import (
        BookingStatus,
        CallOutcome,
        DeliveryStatus,
        EscalationReason,
        EscalationStatus,
        GuardrailAction,
        GuardrailType,
        ProcessingStatus,
        RecordingStatus,
        ToolExecutionStatus,
        TranscriptStatus,
        TurnRole,
        Urgency,
    )
    from ai_database.models import (
        AuditLog,
        Booking,
        Call,
        Escalation,
        GuardrailEvent,
        Message,
        Service,
        ToolExecution,
        Turn,
        UsageRecord,
    )

    engine = create_async_engine(migrated_database)
    tenant_id = seeded_tenants["tenant_a_id"]
    suffix = seeded_tenants["suffix"]
    now = datetime.now(UTC)
    data: dict[str, Any] = {"tenant_id": tenant_id}

    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        service = Service(
            tenant_id=tenant_id,
            name="Drain Cleaning",
            name_normalized=f"drain cleaning {suffix}",
            duration_minutes=60,
        )
        session.add(service)
        await session.flush()

        booked = Call(
            tenant_id=tenant_id,
            provider_call_sid=f"CA_dash1_{suffix}",
            from_number_last_four="4242",
            to_number="+15550002000",
            started_at=now - timedelta(days=1),
            ended_at=now - timedelta(days=1) + timedelta(minutes=4),
            duration_seconds=240,
            outcome=CallOutcome.BOOKED,
            urgency=Urgency.ROUTINE,
            recording_status=RecordingStatus.STORED,
            recording_object_key=f"tenants/{tenant_id}/calls/x/recording.wav",
            transcript_status=TranscriptStatus.COMPLETE,
            post_processing_status=ProcessingStatus.COMPLETE,
            estimated_cost_cents=42,
        )
        messaged = Call(
            tenant_id=tenant_id,
            provider_call_sid=f"CA_dash2_{suffix}",
            from_number_last_four="9911",
            to_number="+15550002000",
            started_at=now - timedelta(hours=3),
            ended_at=now - timedelta(hours=3) + timedelta(minutes=2),
            duration_seconds=120,
            outcome=CallOutcome.MESSAGE_TAKEN,
            urgency=Urgency.EMERGENCY,
            transcript_status=TranscriptStatus.COMPLETE,
            post_processing_status=ProcessingStatus.COMPLETE,
        )
        fresh = Call(
            tenant_id=tenant_id,
            provider_call_sid=f"CA_dash3_{suffix}",
            from_number_last_four="7000",
            to_number="+15550002000",
            started_at=now - timedelta(minutes=10),
            duration_seconds=None,
        )
        session.add_all([booked, messaged, fresh])
        await session.flush()

        session.add_all(
            [
                Turn(
                    tenant_id=tenant_id,
                    call_id=messaged.id,
                    turn_index=0,
                    role=TurnRole.ASSISTANT,
                    text="Thanks for calling!",
                    total_latency_ms=820,
                    llm_ttft_ms=180,
                ),
                Turn(
                    tenant_id=tenant_id,
                    call_id=messaged.id,
                    turn_index=1,
                    role=TurnRole.CALLER,
                    text="My basement is flooding.",
                    barge_in=True,
                    interrupted=True,
                ),
                Booking(
                    tenant_id=tenant_id,
                    call_id=booked.id,
                    service_id=service.id,
                    customer_name="Pat Winters",
                    scheduled_at=now + timedelta(days=2),
                    timezone="UTC",
                    idempotency_key=f"bk_dash_{suffix}",
                    status=BookingStatus.CONFIRMED,
                ),
                Message(
                    tenant_id=tenant_id,
                    call_id=messaged.id,
                    customer_name="Flood Caller",
                    body_encrypted="enc:payload",
                    urgency=Urgency.EMERGENCY,
                    delivery_status=DeliveryStatus.SENT,
                ),
                Escalation(
                    tenant_id=tenant_id,
                    call_id=messaged.id,
                    reason=EscalationReason.EMERGENCY,
                    initiated_at=now - timedelta(hours=3) + timedelta(minutes=1),
                    status=EscalationStatus.INITIATED,
                ),
                GuardrailEvent(
                    tenant_id=tenant_id,
                    call_id=messaged.id,
                    guardrail_type=GuardrailType.EMERGENCY,
                    action=GuardrailAction.ESCALATED,
                    input_redacted={"trigger": "flood"},
                ),
                ToolExecution(
                    tenant_id=tenant_id,
                    call_id=messaged.id,
                    tool_name="take_message",
                    status=ToolExecutionStatus.SUCCESS,
                    started_at=now - timedelta(hours=3) + timedelta(seconds=45),
                    duration_ms=350,
                    input_redacted={"urgency": "emergency"},
                    result_redacted={"stored": True},
                ),
                UsageRecord(
                    tenant_id=tenant_id,
                    call_id=messaged.id,
                    provider="groq",
                    usage_type="llm_postcall_tokens",
                    quantity=512,
                    unit="tokens",
                ),
                AuditLog(
                    tenant_id=tenant_id,
                    action="post_call.processed",
                    actor_external_user_id=None,
                    actor_role="worker",
                    resource_type="call",
                    resource_id=str(messaged.id),
                    after_redacted={
                        "outcome": "message_taken",
                        "sentiment": "frustrated",
                        "follow_up_required": True,
                        "summary": "Caller reported a basement flood; urgent message taken.",
                    },
                ),
            ]
        )
        data.update(booked_id=booked.id, messaged_id=messaged.id, fresh_id=fresh.id)

    try:
        yield data
    finally:
        async with AsyncSession(engine) as session, session.begin():
            for table, column in [
                ("audit_logs", "resource_id"),
            ]:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE {column} = :v"),  # noqa: S608
                    {"v": str(data["messaged_id"])},
                )
            for table in [
                "usage_records",
                "tool_executions",
                "guardrail_events",
                "escalations",
                "messages",
                "bookings",
                "turns",
            ]:
                await session.execute(
                    text(
                        f"DELETE FROM {table} WHERE call_id IN "  # noqa: S608
                        "(SELECT id FROM calls WHERE provider_call_sid LIKE :pat)"
                    ),
                    {"pat": f"CA_dash%_{seeded_tenants['suffix']}"},
                )
            await session.execute(
                text("DELETE FROM calls WHERE provider_call_sid LIKE :pat"),
                {"pat": f"CA_dash%_{seeded_tenants['suffix']}"},
            )
            await session.execute(
                text("DELETE FROM services WHERE tenant_id = :tid"),
                {"tid": data["tenant_id"]},
            )
        await engine.dispose()


@pytest.fixture
def owner_headers(mint_token: Callable[..., str], seeded_tenants: dict[str, Any]) -> dict[str, str]:
    return _auth(mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"]))


@pytest.fixture
def admin_headers(mint_token: Callable[..., str]) -> dict[str, str]:
    return _auth(mint_token(sub="admin_user", platform_role="platform_admin"))


async def test_list_shows_all_columns(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get("/tenant/calls", headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    by_id = {item["id"]: item for item in body["items"]}

    booked = by_id[str(seeded_calls["booked_id"])]
    assert booked["outcome"] == "booked"
    assert booked["service"] == "Drain Cleaning"
    assert booked["booking_status"] == "confirmed"
    assert booked["recording_available"] is True
    assert booked["from_number_last_four"] == "4242"

    messaged = by_id[str(seeded_calls["messaged_id"])]
    assert messaged["urgency"] == "emergency"
    assert messaged["transfer_status"] == "initiated"
    assert messaged["recording_available"] is False
    assert messaged["processing_status"] == "complete"

    fresh = by_id[str(seeded_calls["fresh_id"])]
    assert fresh["outcome"] is None
    assert fresh["processing_status"] == "pending"


async def test_list_sorted_newest_first_by_default(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get("/tenant/calls", headers=owner_headers)
    items = response.json()["items"]
    starts = [item["started_at"] for item in items]
    assert starts == sorted(starts, reverse=True)


async def test_outcome_and_urgency_filters(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/tenant/calls", params={"outcome": "booked"}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_calls["booked_id"])]

    response = await client.get(
        "/tenant/calls", params={"urgency": "emergency"}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_calls["messaged_id"])]


async def test_booking_filter(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/tenant/calls", params={"booking": "confirmed"}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_calls["booked_id"])]

    response = await client.get("/tenant/calls", params={"booking": "none"}, headers=owner_headers)
    ids = {i["id"] for i in response.json()["items"]}
    assert str(seeded_calls["booked_id"]) not in ids
    assert len(ids) == 2


async def test_search_by_last_four_digits(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get("/tenant/calls", params={"search": "9911"}, headers=owner_headers)
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_calls["messaged_id"])]


async def test_search_by_customer_name(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/tenant/calls", params={"search": "winters"}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_calls["booked_id"])]


async def test_date_filter_and_duration_sort(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    cutoff = (datetime.now(UTC) - timedelta(hours=12)).isoformat()
    response = await client.get(
        "/tenant/calls", params={"date_from": cutoff}, headers=owner_headers
    )
    ids = {i["id"] for i in response.json()["items"]}
    assert str(seeded_calls["booked_id"]) not in ids
    assert len(ids) == 2

    response = await client.get(
        "/tenant/calls", params={"sort": "-duration"}, headers=owner_headers
    )
    durations = [i["duration_seconds"] for i in response.json()["items"]]
    assert durations[0] == 240
    assert durations[-1] is None  # null durations sort last


async def test_pagination(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get("/tenant/calls", params={"page_size": "2"}, headers=owner_headers)
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    response = await client.get(
        "/tenant/calls", params={"page_size": "2", "page": "2"}, headers=owner_headers
    )
    assert len(response.json()["items"]) == 1


async def test_csv_export(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/tenant/calls/export", params={"outcome": "booked"}, headers=owner_headers
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().split("\n")
    assert lines[0].startswith("started_at,caller_last_four,duration_seconds,outcome")
    assert len(lines) == 2
    assert "booked" in lines[1]
    assert "4242" in lines[1]


async def test_client_detail_hides_technical_fields(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"/tenant/calls/{seeded_calls['messaged_id']}", headers=owner_headers
    )
    assert response.status_code == 200
    body = response.json()

    assert body["summary"] == "Caller reported a basement flood; urgent message taken."
    assert body["sentiment"] == "frustrated"
    assert body["follow_up_required"] is True
    assert [t["role"] for t in body["turns"]] == ["assistant", "caller"]
    assert body["turns"][1]["interrupted"] is True
    assert body["message"]["customer_name"] == "Flood Caller"
    assert body["escalation"]["status"] == "initiated"
    assert body["guardrails"][0]["guardrail_type"] == "emergency"
    assert body["tools"][0]["tool_name"] == "take_message"
    assert body["usage"][0]["quantity"] == 512

    # Technical details are hidden from clients.
    assert body["provider_call_sid"] is None
    assert body["failure_category"] is None
    assert body["turns"][0]["total_latency_ms"] is None
    assert body["tools"][0]["input_redacted"] is None
    assert body["guardrails"][0]["input_redacted"] is None
    assert body["usage"][0]["provider"] is None

    # Timeline is chronological and covers related records.
    kinds = [entry["kind"] for entry in body["timeline"]]
    assert kinds[0] == "call"
    assert {"tool", "guardrail", "message", "escalation"} <= set(kinds)
    times = [entry["at"] for entry in body["timeline"]]
    assert times == sorted(times)


async def test_admin_detail_expands_technical_fields(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], admin_headers: dict[str, str]
) -> None:
    tenant_id = seeded_calls["tenant_id"]
    response = await client.get(
        f"/admin/tenants/{tenant_id}/calls/{seeded_calls['messaged_id']}",
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["provider_call_sid"].startswith("CA_dash2_")
    assert body["turns"][0]["total_latency_ms"] == 820
    assert body["turns"][0]["llm_ttft_ms"] == 180
    assert body["tools"][0]["input_redacted"] == {"urgency": "emergency"}
    assert body["tools"][0]["result_redacted"] == {"stored": True}
    assert body["guardrails"][0]["input_redacted"] == {"trigger": "flood"}
    assert body["usage"][0]["provider"] == "groq"
    assert body["recording_status"] is not None


async def test_admin_list_and_unknown_tenant(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"/admin/tenants/{seeded_calls['tenant_id']}/calls", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["total"] == 3

    response = await client.get(f"/admin/tenants/{uuid.uuid4()}/calls", headers=admin_headers)
    assert response.status_code == 404


async def test_client_cannot_reach_admin_call_routes(
    client: httpx.AsyncClient, seeded_calls: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"/admin/tenants/{seeded_calls['tenant_id']}/calls", headers=owner_headers
    )
    assert response.status_code == 404


async def test_cross_tenant_call_detail_is_404(
    client: httpx.AsyncClient,
    seeded_tenants: dict[str, Any],
    seeded_calls: dict[str, Any],
    mint_token: Callable[..., str],
) -> None:
    token = mint_token(sub=seeded_tenants["owner_s"], org_id=seeded_tenants["org_s"])
    response = await client.get(f"/tenant/calls/{seeded_calls['booked_id']}", headers=_auth(token))
    assert response.status_code in (403, 404)
