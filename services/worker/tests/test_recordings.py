"""Recording lifecycle tests: ingest, corrupt/failed paths, signed
access with audit, and retention sweeps with legal hold."""

# Reuse the DB availability marker from the API test helpers pattern.
import os
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from ai_providers.errors import ProviderUnavailableError
from ai_providers.storage import MockStorageProvider
from ai_providers.telephony import MockTelephonyProvider, RecordingMetadata
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from worker.recordings import (
    DEFAULT_RETENTION_DAYS,
    ingest_recording,
    resolve_retention_days,
    retention_sweep,
)

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

WAV_BODY = b"RIFF" + b"\x00" * 2048


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
    sid = f"CA_rec_{uuid.uuid4().hex[:10]}"
    await db.execute(
        text(
            "INSERT INTO tenants (id, name, slug, vertical, timezone, status) "
            "VALUES (:tid, 'Rec Test', :slug, 'plumbing', 'UTC', 'active')"
        ),
        {"tid": tenant_id, "slug": f"rec-{uuid.uuid4().hex[:8]}"},
    )
    await db.execute(
        text(
            "INSERT INTO calls (id, tenant_id, provider_call_sid, to_number, started_at, "
            "ended_at, direction, transport, recording_status, recording_legal_hold, "
            "transcript_status, post_processing_status) "
            "VALUES (:cid, :tid, :sid, '+15550001000', now() - interval '40 days', "
            "now() - interval '40 days', 'inbound', 'phone', 'in_progress', false, "
            "'pending', 'pending')"
        ),
        {"cid": call_id, "tid": tenant_id, "sid": sid},
    )
    return {"tenant_id": tenant_id, "call_id": call_id, "sid": sid}


def _telephony_with_recording(sid: str) -> MockTelephonyProvider:
    provider = MockTelephonyProvider()
    provider.recordings[sid] = RecordingMetadata(
        provider_call_sid=sid,
        recording_sid="RE123",
        duration_seconds=63,
        download_url="https://recordings.example/RE123.wav",
    )
    return provider


def _download_client(body: bytes = WAV_BODY, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_ingest_stores_key_only_and_marks_stored(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    storage = MockStorageProvider()
    status = await ingest_recording(
        db,
        call_id=call_row["call_id"],
        storage=storage,
        telephony=_telephony_with_recording(call_row["sid"]),
        download=_download_client(),
    )
    assert status.value == "stored"

    expected_key = f"tenants/{call_row['tenant_id']}/calls/{call_row['call_id']}/recording.wav"
    assert expected_key in storage.objects
    stored, data = storage.objects[expected_key]
    assert data == WAV_BODY
    assert stored.retain_until is not None  # retention metadata applied

    row = (
        await db.execute(
            text("SELECT recording_object_key FROM calls WHERE id = :cid"),
            {"cid": call_row["call_id"]},
        )
    ).one()
    assert row[0] == expected_key  # only the key in PostgreSQL


async def test_ingest_not_ready_marks_pending(db: AsyncSession, call_row: dict[str, Any]) -> None:
    status = await ingest_recording(
        db,
        call_id=call_row["call_id"],
        storage=MockStorageProvider(),
        telephony=MockTelephonyProvider(),  # no recording yet
        download=_download_client(),
    )
    assert status.value == "pending_fetch"


async def test_ingest_corrupt_body_marks_failed(db: AsyncSession, call_row: dict[str, Any]) -> None:
    status = await ingest_recording(
        db,
        call_id=call_row["call_id"],
        storage=MockStorageProvider(),
        telephony=_telephony_with_recording(call_row["sid"]),
        download=_download_client(body=b"not-audio"),
    )
    assert status.value == "failed"


async def test_ingest_download_error_marks_failed(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    status = await ingest_recording(
        db,
        call_id=call_row["call_id"],
        storage=MockStorageProvider(),
        telephony=_telephony_with_recording(call_row["sid"]),
        download=_download_client(status=503),
    )
    assert status.value == "failed"


async def test_ingest_upload_failure_marks_failed_for_retry(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    storage = MockStorageProvider()

    async def broken_upload(**kwargs: Any) -> Any:
        raise ProviderUnavailableError("r2 down", provider="r2")

    storage.upload = broken_upload  # type: ignore[method-assign]
    status = await ingest_recording(
        db,
        call_id=call_row["call_id"],
        storage=storage,
        telephony=_telephony_with_recording(call_row["sid"]),
        download=_download_client(),
    )
    assert status.value == "failed"
    # FAILED is in the retryable set — a later ingest attempt may succeed.


async def test_signed_access_authorizes_and_audits(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    from api.services.recordings import sign_recording_url

    storage = MockStorageProvider()
    await ingest_recording(
        db,
        call_id=call_row["call_id"],
        storage=storage,
        telephony=_telephony_with_recording(call_row["sid"]),
        download=_download_client(),
    )

    url = await sign_recording_url(
        db,
        call_id=call_row["call_id"],
        tenant_id=call_row["tenant_id"],
        storage=storage,
        actor_external_user_id="owner_x",
        actor_role="client_owner",
    )
    assert url is not None and "exp=" in url

    audits = (
        await db.execute(
            text(
                "SELECT count(*) FROM audit_logs WHERE tenant_id = :tid "
                "AND action = 'recording.accessed'"
            ),
            {"tid": call_row["tenant_id"]},
        )
    ).scalar_one()
    assert audits == 1

    # Cross-tenant signing attempt yields nothing.
    stranger = await sign_recording_url(
        db,
        call_id=call_row["call_id"],
        tenant_id=uuid.uuid4(),
        storage=storage,
        actor_external_user_id="intruder",
        actor_role="client_owner",
    )
    assert stranger is None


async def test_retention_sweep_deletes_expired_with_audit(
    db: AsyncSession, call_row: dict[str, Any]
) -> None:
    storage = MockStorageProvider()
    await ingest_recording(
        db,
        call_id=call_row["call_id"],
        storage=storage,
        telephony=_telephony_with_recording(call_row["sid"]),
        download=_download_client(),
    )

    # Call ended 40 days ago; default retention is 30 → eligible.
    deleted = await retention_sweep(db, storage=storage)
    assert deleted == 1
    assert storage.objects == {}

    row = (
        await db.execute(
            text("SELECT recording_status, recording_object_key FROM calls WHERE id = :cid"),
            {"cid": call_row["call_id"]},
        )
    ).one()
    assert row[0] == "deleted"
    assert row[1] is None

    audits = (
        await db.execute(
            text(
                "SELECT count(*) FROM audit_logs WHERE tenant_id = :tid "
                "AND action = 'recording.retention_deleted'"
            ),
            {"tid": call_row["tenant_id"]},
        )
    ).scalar_one()
    assert audits == 1


async def test_legal_hold_exempts_from_sweep(db: AsyncSession, call_row: dict[str, Any]) -> None:
    storage = MockStorageProvider()
    await ingest_recording(
        db,
        call_id=call_row["call_id"],
        storage=storage,
        telephony=_telephony_with_recording(call_row["sid"]),
        download=_download_client(),
    )
    await db.execute(
        text("UPDATE calls SET recording_legal_hold = true WHERE id = :cid"),
        {"cid": call_row["call_id"]},
    )
    deleted = await retention_sweep(db, storage=storage)
    assert deleted == 0
    assert len(storage.objects) == 1  # untouched


async def test_recent_recording_not_swept(db: AsyncSession, call_row: dict[str, Any]) -> None:
    storage = MockStorageProvider()
    await ingest_recording(
        db,
        call_id=call_row["call_id"],
        storage=storage,
        telephony=_telephony_with_recording(call_row["sid"]),
        download=_download_client(),
    )
    await db.execute(
        text("UPDATE calls SET ended_at = now() WHERE id = :cid"),
        {"cid": call_row["call_id"]},
    )
    deleted = await retention_sweep(db, storage=storage)
    assert deleted == 0


def test_retention_defaults_and_cap() -> None:
    assert resolve_retention_days(None) == DEFAULT_RETENTION_DAYS

    class FakeConfig:
        recording_retention_days = 90

    assert resolve_retention_days(FakeConfig()) == 90  # type: ignore[arg-type]

    class TooLong:
        recording_retention_days = 365

    assert resolve_retention_days(TooLong()) == 90  # type: ignore[arg-type]
