"""Post-call pipeline tests: extraction, idempotency, malformed output,
booking authority, notifications, and usage accounting."""

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from ai_providers.errors import ProviderTimeoutError
from ai_providers.llm import ChatMessage, LLMStream, MockLLMProvider, MockTurn, ToolSpec
from ai_providers.messaging import MockEmailProvider
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from worker.post_call import assemble_transcript, process_call

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:test@localhost:55432/receptionist_test",
)


def _db_reachable() -> bool:
    from sqlalchemy.engine import make_url

    url = make_url(TEST_DATABASE_URL)
    try:
        with socket.create_connection((url.host or "localhost", url.port or 5432), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="test database not reachable")

EXTRACTION: dict[str, Any] = {
    "intent": "book a service visit",
    "service": "drain cleaning",
    "urgency": "routine",
    "outcome": "booked",
    "customer_name": "Pat",
    "phone_available": True,
    "address_available": True,
    "booking_status": "confirmed",
    "scheduled_time": "2026-07-29T10:00:00",
    "message_status": "none",
    "escalation_status": "none",
    "unresolved_questions": [],
    "sentiment": "positive",
    "follow_up_required": False,
    "summary": "Caller booked a routine drain cleaning for Tuesday morning.",
}


def scripted_llm(extraction: dict[str, Any] | None = None, raw: str | None = None) -> Any:
    body = raw if raw is not None else json.dumps(extraction or EXTRACTION)
    return MockLLMProvider(turns=[MockTurn(text=body)])


class TimeoutLLM:
    async def stream(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 300,
    ) -> LLMStream:
        raise ProviderTimeoutError("post-call model timed out")


@pytest.fixture(scope="session")
def migrated() -> str:
    from alembic import command
    from alembic.config import Config

    os.environ["DATABASE_DIRECT_URL"] = TEST_DATABASE_URL
    command.upgrade(Config("alembic.ini"), "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def db(migrated: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(migrated)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        yield session
        await session.close()
        await trans.rollback()
    await engine.dispose()


@pytest.fixture
async def call_row(db: AsyncSession) -> dict[str, Any]:
    tenant_id, call_id = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO tenants (id, name, slug, vertical, timezone, status) "
            "VALUES (:tid, 'PostCall Test', :slug, 'plumbing', 'UTC', 'active')"
        ),
        {"tid": tenant_id, "slug": f"pc-{uuid.uuid4().hex[:8]}"},
    )
    await db.execute(
        text(
            "INSERT INTO tenant_config (tenant_id, language, recording_enabled, "
            "max_call_seconds, configuration_version, notification_email) "
            "VALUES (:tid, 'en', false, 900, 1, 'owner@example.com')"
        ),
        {"tid": tenant_id},
    )
    await db.execute(
        text(
            "INSERT INTO calls (id, tenant_id, provider_call_sid, to_number, started_at, "
            "ended_at, direction, transport, recording_status, recording_legal_hold, "
            "transcript_status, post_processing_status) "
            "VALUES (:cid, :tid, :sid, '+15550001000', now() - interval '10 minutes', "
            "now() - interval '5 minutes', 'inbound', 'phone', 'disabled', false, "
            "'partial', 'pending')"
        ),
        {"cid": call_id, "tid": tenant_id, "sid": f"CA_pc_{uuid.uuid4().hex[:10]}"},
    )
    for index, (role, line) in enumerate(
        [
            ("assistant", "Thanks for calling, how can I help?"),
            ("caller", "My kitchen drain is clogged, can someone come Tuesday?"),
            ("assistant", "We can do Tuesday at ten, shall I book it?"),
            ("caller", "Yes please, I'm Pat."),
        ]
    ):
        await db.execute(
            text(
                "INSERT INTO turns (id, tenant_id, call_id, turn_index, role, text, "
                "barge_in, interrupted) "
                "VALUES (:id, :tid, :cid, :idx, :role, :text, false, false)"
            ),
            {
                "id": uuid.uuid4(),
                "tid": tenant_id,
                "cid": call_id,
                "idx": index,
                "role": role,
                "text": line,
            },
        )
    return {"tenant_id": tenant_id, "call_id": call_id}


async def _call_state(db: AsyncSession, call_id: uuid.UUID) -> Any:
    return (
        await db.execute(
            text(
                "SELECT outcome, urgency, transcript_status, post_processing_status "
                "FROM calls WHERE id = :cid"
            ),
            {"cid": call_id},
        )
    ).one()


async def _audit_count(db: AsyncSession, action: str, call_id: uuid.UUID) -> int:
    count = (
        await db.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = :action AND resource_id = :rid"),
            {"action": action, "rid": str(call_id)},
        )
    ).scalar_one()
    return int(count)


async def test_pipeline_completes_and_updates_call(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    status = await process_call(db, call_id=call_row["call_id"], llm=scripted_llm())
    assert status.value == "complete"

    row = await _call_state(db, call_row["call_id"])
    assert row.outcome == "booked"
    assert row.urgency == "routine"
    assert row.transcript_status == "complete"
    assert row.post_processing_status == "complete"
    assert await _audit_count(db, "post_call.processed", call_row["call_id"]) == 1


async def test_usage_recorded_for_extraction_tokens(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    await process_call(db, call_id=call_row["call_id"], llm=scripted_llm())
    quantity = (
        await db.execute(
            text(
                "SELECT quantity FROM usage_records "
                "WHERE call_id = :cid AND usage_type = 'llm_postcall_tokens'"
            ),
            {"cid": call_row["call_id"]},
        )
    ).scalar_one()
    assert quantity > 0


async def test_duplicate_delivery_is_idempotent(db: AsyncSession, call_row: dict[str, Any]) -> None:
    llm = scripted_llm()
    await process_call(db, call_id=call_row["call_id"], llm=llm)
    status = await process_call(db, call_id=call_row["call_id"], llm=llm)
    assert status.value == "complete"
    # Second delivery short-circuits: no extra LLM call, no extra audit.
    assert len(llm.requests) == 1
    assert await _audit_count(db, "post_call.processed", call_row["call_id"]) == 1


async def test_malformed_output_dead_letters(db: AsyncSession, call_row: dict[str, Any]) -> None:
    status = await process_call(
        db, call_id=call_row["call_id"], llm=scripted_llm(raw="sorry, I cannot do JSON")
    )
    assert status.value == "failed"
    row = await _call_state(db, call_row["call_id"])
    assert row.post_processing_status == "failed"
    assert row.outcome is None  # nothing corrupted
    assert await _audit_count(db, "post_call.extraction_failed", call_row["call_id"]) == 1


async def test_provider_timeout_marks_failed_for_retry(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    status = await process_call(db, call_id=call_row["call_id"], llm=TimeoutLLM())
    assert status.value == "failed"


async def test_confirmed_booking_is_never_overwritten(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    await db.execute(
        text(
            "INSERT INTO bookings (id, tenant_id, call_id, scheduled_at, timezone, "
            "idempotency_key, status, reconciliation_status) "
            "VALUES (:id, :tid, :cid, now() + interval '2 days', 'UTC', :key, "
            "'confirmed', 'not_required')"
        ),
        {
            "id": uuid.uuid4(),
            "tid": call_row["tenant_id"],
            "cid": call_row["call_id"],
            "key": f"bk-{uuid.uuid4().hex[:12]}",
        },
    )
    # Extraction wrongly claims no booking happened; the committed
    # booking is authoritative and wins.
    wrong = dict(EXTRACTION, outcome="caller_hangup", booking_status="none")
    await process_call(db, call_id=call_row["call_id"], llm=scripted_llm(wrong))

    row = await _call_state(db, call_row["call_id"])
    assert row.outcome == "booked"
    booking_status = (
        await db.execute(
            text("SELECT status FROM bookings WHERE call_id = :cid"),
            {"cid": call_row["call_id"]},
        )
    ).scalar_one()
    assert booking_status == "confirmed"


async def test_message_notification_sent_and_marked_delivered(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    await db.execute(
        text(
            "INSERT INTO messages (id, tenant_id, call_id, body_encrypted, urgency, "
            "delivery_status) "
            "VALUES (:id, :tid, :cid, 'enc:payload', 'routine', 'pending')"
        ),
        {"id": uuid.uuid4(), "tid": call_row["tenant_id"], "cid": call_row["call_id"]},
    )
    email = MockEmailProvider()
    message_extraction = dict(
        EXTRACTION,
        outcome="message_taken",
        booking_status="none",
        message_status="taken",
    )
    await process_call(
        db,
        call_id=call_row["call_id"],
        llm=scripted_llm(message_extraction),
        email=email,
        notify_to="owner@example.com",
    )

    assert len(email.sent) == 1
    assert email.sent[0]["template"] == "new_message"
    delivery = (
        await db.execute(
            text("SELECT delivery_status FROM messages WHERE call_id = :cid"),
            {"cid": call_row["call_id"]},
        )
    ).scalar_one()
    assert delivery == "sent"


async def test_notification_deduplicated_across_deliveries(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    email = MockEmailProvider()
    # The idempotency key this job will use was already consumed by a
    # prior delivery attempt; the duplicate is swallowed, job completes.
    await email.send_template(
        to_email="owner@example.com",
        template="booking_confirmation",
        variables={"summary": "s"},
        idempotency_key=f"postcall:{call_row['call_id']}:booking_confirmation",
    )
    status = await process_call(
        db,
        call_id=call_row["call_id"],
        llm=scripted_llm(),
        email=email,
        notify_to="owner@example.com",
    )
    assert status.value == "complete"
    assert len(email.sent) == 1  # no second send


async def test_urgent_message_uses_escalation_template(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    email = MockEmailProvider()
    urgent = dict(
        EXTRACTION,
        outcome="message_taken",
        booking_status="none",
        message_status="taken",
        urgency="emergency",
    )
    await process_call(
        db,
        call_id=call_row["call_id"],
        llm=scripted_llm(urgent),
        email=email,
        notify_to="owner@example.com",
    )
    assert email.sent[0]["template"] == "urgent_escalation"


async def test_unknown_call_raises_lookup_error(db: AsyncSession) -> None:
    with pytest.raises(LookupError):
        await process_call(db, call_id=uuid.uuid4(), llm=scripted_llm())


def test_assemble_transcript_orders_and_labels() -> None:
    from ai_database.enums import TurnRole
    from ai_database.models import Turn

    turns = [
        Turn(turn_index=1, role=TurnRole.CALLER, text="Second line"),
        Turn(turn_index=0, role=TurnRole.ASSISTANT, text="First line"),
        Turn(turn_index=2, role=TurnRole.SYSTEM, text="internal note"),
        Turn(turn_index=3, role=TurnRole.CALLER, text=None),
    ]
    transcript = assemble_transcript(turns)
    assert transcript == "Receptionist: First line\nCaller: Second line"
