"""Bookings and messages dashboard API: filters, export, guarded
cancellation, review state, and the encrypted internal note."""

import base64
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

DATA_KEY = base64.b64encode(b"D" * 32).decode()
HASH_KEY = base64.b64encode(b"H" * 32).decode()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _crypto() -> Any:
    from ai_shared.crypto import AesGcmEncryptionService

    return AesGcmEncryptionService(data_key_b64=DATA_KEY, hash_key_b64=HASH_KEY)


@pytest.fixture(autouse=True)
def encryption_keys() -> Iterator[None]:
    """The dashboard decrypts addresses, bodies, and notes — the app needs
    the same keys the fixtures encrypt with."""
    from api.settings import get_settings

    os.environ["DATA_ENCRYPTION_KEY"] = DATA_KEY
    os.environ["LOOKUP_HASH_KEY"] = HASH_KEY
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def seeded_records(
    migrated_database: str, seeded_tenants: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """Two bookings and two messages in tenant A."""
    from ai_database.enums import (
        BookingStatus,
        DeliveryStatus,
        ReconciliationStatus,
        Urgency,
    )
    from ai_database.models import Booking, Call, Message, Service

    engine = create_async_engine(migrated_database)
    tenant_id = seeded_tenants["tenant_a_id"]
    suffix = seeded_tenants["suffix"]
    crypto = _crypto()
    now = datetime.now(UTC)
    data: dict[str, Any] = {"tenant_id": tenant_id}

    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        service = Service(
            tenant_id=tenant_id,
            name="Water Heater Repair",
            name_normalized=f"water heater repair {suffix}",
            duration_minutes=90,
        )
        call = Call(
            tenant_id=tenant_id,
            provider_call_sid=f"CA_rec_{suffix}",
            to_number="+15550003000",
            started_at=now - timedelta(hours=2),
        )
        session.add_all([service, call])
        await session.flush()

        upcoming = Booking(
            tenant_id=tenant_id,
            call_id=call.id,
            service_id=service.id,
            customer_name="Dana Reyes",
            customer_phone_last_four="3311",
            address_encrypted=crypto.encrypt("18 Cedar Lane, Springfield"),
            scheduled_at=now + timedelta(days=3),
            timezone="America/New_York",
            idempotency_key=f"bk_up_{suffix}",
            status=BookingStatus.CONFIRMED,
            external_calendar_event_id="gcal_evt_1",
            reconciliation_status=ReconciliationStatus.NOT_REQUIRED,
        )
        past = Booking(
            tenant_id=tenant_id,
            customer_name="Sam Cole",
            customer_phone_last_four="7788",
            scheduled_at=now - timedelta(days=5),
            timezone="UTC",
            idempotency_key=f"bk_past_{suffix}",
            status=BookingStatus.PENDING,
        )
        urgent = Message(
            tenant_id=tenant_id,
            call_id=call.id,
            customer_name="Robin Vale",
            customer_phone_last_four="5544",
            body_encrypted=crypto.encrypt("No hot water since this morning."),
            urgency=Urgency.EMERGENCY,
            delivery_status=DeliveryStatus.SENT,
        )
        routine = Message(
            tenant_id=tenant_id,
            customer_name="Casey Lim",
            customer_phone_last_four="1122",
            body_encrypted=crypto.encrypt("Wants a quote for a new install."),
            urgency=Urgency.ROUTINE,
            delivery_status=DeliveryStatus.PENDING,
            reviewed_at=now - timedelta(hours=1),
        )
        session.add_all([upcoming, past, urgent, routine])
        await session.flush()
        data.update(
            call_id=call.id,
            upcoming_id=upcoming.id,
            past_id=past.id,
            urgent_id=urgent.id,
            routine_id=routine.id,
        )

    try:
        yield data
    finally:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                text("DELETE FROM audit_logs WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
            await session.execute(
                text("DELETE FROM messages WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
            await session.execute(
                text("DELETE FROM bookings WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
            await session.execute(
                text("DELETE FROM calls WHERE provider_call_sid = :sid"),
                {"sid": f"CA_rec_{suffix}"},
            )
            await session.execute(
                text("DELETE FROM services WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
        await engine.dispose()


@pytest.fixture
def owner_headers(mint_token: Callable[..., str], seeded_tenants: dict[str, Any]) -> dict[str, str]:
    return _auth(mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"]))


@pytest.fixture
def staff_headers(mint_token: Callable[..., str], seeded_tenants: dict[str, Any]) -> dict[str, str]:
    return _auth(mint_token(sub=seeded_tenants["staff_a"], org_id=seeded_tenants["org_a"]))


# --- Bookings ---------------------------------------------------------------


async def test_booking_list_shows_all_columns(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get("/tenant/bookings", headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2

    upcoming = next(i for i in body["items"] if i["id"] == str(seeded_records["upcoming_id"]))
    assert upcoming["customer_name"] == "Dana Reyes"
    assert upcoming["phone_last_four"] == "3311"
    assert upcoming["service"] == "Water Heater Repair"
    assert upcoming["timezone"] == "America/New_York"
    assert upcoming["address"] == "18 Cedar Lane, Springfield"
    assert upcoming["calendar_status"] == "linked"
    assert upcoming["status"] == "confirmed"
    assert upcoming["call_id"] == str(seeded_records["call_id"])

    past = next(i for i in body["items"] if i["id"] == str(seeded_records["past_id"]))
    assert past["calendar_status"] == "not_linked"
    assert past["address"] is None


async def test_booking_filters_and_search(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/tenant/bookings", params={"status": "confirmed"}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_records["upcoming_id"])]

    response = await client.get(
        "/tenant/bookings", params={"search": "7788"}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_records["past_id"])]

    response = await client.get(
        "/tenant/bookings", params={"search": "dana"}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_records["upcoming_id"])]

    cutoff = datetime.now(UTC).isoformat()
    response = await client.get(
        "/tenant/bookings", params={"date_from": cutoff}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_records["upcoming_id"])]


async def test_booking_export_csv(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/tenant/bookings/export", params={"status": "confirmed"}, headers=owner_headers
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().split("\n")
    assert lines[0].startswith("customer_name,phone_last_four,service")
    assert len(lines) == 2
    assert "Dana Reyes" in lines[1]


async def test_cancellation_requires_confirmation(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/tenant/bookings/{seeded_records['upcoming_id']}/cancel",
        json={"confirm": False},
        headers=owner_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


async def test_staff_cannot_cancel(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], staff_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/tenant/bookings/{seeded_records['upcoming_id']}/cancel",
        json={"confirm": True},
        headers=staff_headers,
    )
    assert response.status_code in (403, 404)


async def test_confirmed_cancellation_flags_reconciliation_and_audits(
    client: httpx.AsyncClient,
    seeded_records: dict[str, Any],
    owner_headers: dict[str, str],
    migrated_database: str,
) -> None:
    response = await client.post(
        f"/tenant/bookings/{seeded_records['upcoming_id']}/cancel",
        json={"confirm": True},
        headers=owner_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    # A calendar event exists, so the reconciliation worker must clean up.
    assert body["reconciliation_status"] == "pending"

    engine = create_async_engine(migrated_database)
    async with AsyncSession(engine) as session:
        # The row survives — cancellation is never a delete.
        status = (
            await session.execute(
                text("SELECT status FROM bookings WHERE id = :bid"),
                {"bid": seeded_records["upcoming_id"]},
            )
        ).scalar_one()
        assert status == "cancelled"
        audits = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE action = 'booking.cancelled' AND resource_id = :rid"
                ),
                {"rid": str(seeded_records["upcoming_id"])},
            )
        ).scalar_one()
        assert audits == 1
    await engine.dispose()


async def test_double_cancellation_conflicts(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    url = f"/tenant/bookings/{seeded_records['upcoming_id']}/cancel"
    first = await client.post(url, json={"confirm": True}, headers=owner_headers)
    assert first.status_code == 200
    second = await client.post(url, json={"confirm": True}, headers=owner_headers)
    assert second.status_code == 409


async def test_cross_tenant_cancellation_is_404(
    client: httpx.AsyncClient,
    seeded_records: dict[str, Any],
    seeded_tenants: dict[str, Any],
    mint_token: Callable[..., str],
) -> None:
    token = mint_token(sub=seeded_tenants["owner_s"], org_id=seeded_tenants["org_s"])
    response = await client.post(
        f"/tenant/bookings/{seeded_records['upcoming_id']}/cancel",
        json={"confirm": True},
        headers=_auth(token),
    )
    assert response.status_code in (403, 404)


# --- Messages ---------------------------------------------------------------


async def test_message_list_decrypts_body(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get("/tenant/messages", headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2

    urgent = next(i for i in body["items"] if i["id"] == str(seeded_records["urgent_id"]))
    assert urgent["customer_name"] == "Robin Vale"
    assert urgent["phone_last_four"] == "5544"
    assert urgent["body"] == "No hot water since this morning."
    assert urgent["urgency"] == "emergency"
    assert urgent["delivery_status"] == "sent"
    assert urgent["reviewed_at"] is None
    assert urgent["call_id"] == str(seeded_records["call_id"])
    assert urgent["internal_note"] is None


async def test_message_urgency_and_reviewed_filters(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/tenant/messages", params={"urgency": "emergency"}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_records["urgent_id"])]

    response = await client.get(
        "/tenant/messages", params={"reviewed": "no"}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_records["urgent_id"])]

    response = await client.get(
        "/tenant/messages", params={"reviewed": "yes"}, headers=owner_headers
    )
    assert [i["id"] for i in response.json()["items"]] == [str(seeded_records["routine_id"])]


async def test_mark_reviewed_and_unreviewed(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], staff_headers: dict[str, str]
) -> None:
    url = f"/tenant/messages/{seeded_records['urgent_id']}/review"
    response = await client.post(url, json={"reviewed": True}, headers=staff_headers)
    assert response.status_code == 200
    assert response.json()["reviewed_at"] is not None

    response = await client.post(url, json={"reviewed": False}, headers=staff_headers)
    assert response.json()["reviewed_at"] is None


async def test_internal_note_round_trips_encrypted(
    client: httpx.AsyncClient,
    seeded_records: dict[str, Any],
    owner_headers: dict[str, str],
    migrated_database: str,
) -> None:
    note = "Called back; scheduling for Thursday."
    response = await client.put(
        f"/tenant/messages/{seeded_records['urgent_id']}/note",
        json={"note": note},
        headers=owner_headers,
    )
    assert response.status_code == 200
    assert response.json()["internal_note"] == note

    engine = create_async_engine(migrated_database)
    async with AsyncSession(engine) as session:
        stored = (
            await session.execute(
                text("SELECT internal_note_encrypted FROM messages WHERE id = :mid"),
                {"mid": seeded_records["urgent_id"]},
            )
        ).scalar_one()
        # Stored ciphertext never contains the plaintext.
        assert stored.startswith("v1:")
        assert note not in stored
        # The note text is never copied into the audit trail.
        audit_payload = (
            await session.execute(
                text(
                    "SELECT after_redacted FROM audit_logs "
                    "WHERE action = 'message.note_updated' AND resource_id = :rid"
                ),
                {"rid": str(seeded_records["urgent_id"])},
            )
        ).scalar_one()
        assert audit_payload == {"has_note": True}
    await engine.dispose()


async def test_note_is_not_visible_to_the_conversation_engine(
    client: httpx.AsyncClient,
    seeded_records: dict[str, Any],
    owner_headers: dict[str, str],
    migrated_database: str,
) -> None:
    """The receptionist must never be able to read an internal note aloud:
    the tool-persistence layer that feeds conversations does not select
    the column at all."""
    await client.put(
        f"/tenant/messages/{seeded_records['urgent_id']}/note",
        json={"note": "Do not mention: customer disputes last invoice."},
        headers=owner_headers,
    )
    from pathlib import Path

    persistence = Path("packages/database/src/ai_database/tool_persistence.py").read_text(
        encoding="utf-8"
    )
    assert "internal_note" not in persistence


async def test_clearing_note_removes_it(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    url = f"/tenant/messages/{seeded_records['urgent_id']}/note"
    await client.put(url, json={"note": "temporary"}, headers=owner_headers)
    response = await client.put(url, json={"note": "   "}, headers=owner_headers)
    assert response.json()["internal_note"] is None


async def test_message_export_csv(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/tenant/messages/export", params={"urgency": "emergency"}, headers=owner_headers
    )
    assert response.status_code == 200
    lines = response.text.strip().split("\n")
    assert lines[0].startswith("customer_name,phone_last_four,message")
    assert len(lines) == 2
    assert "No hot water since this morning." in lines[1]


async def test_cross_tenant_message_note_is_404(
    client: httpx.AsyncClient,
    seeded_records: dict[str, Any],
    seeded_tenants: dict[str, Any],
    mint_token: Callable[..., str],
) -> None:
    token = mint_token(sub=seeded_tenants["owner_s"], org_id=seeded_tenants["org_s"])
    response = await client.put(
        f"/tenant/messages/{seeded_records['urgent_id']}/note",
        json={"note": "should not land"},
        headers=_auth(token),
    )
    assert response.status_code in (403, 404)


async def test_unknown_message_is_404(
    client: httpx.AsyncClient, seeded_records: dict[str, Any], owner_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/tenant/messages/{uuid.uuid4()}/review",
        json={"reviewed": True},
        headers=owner_headers,
    )
    assert response.status_code == 404
