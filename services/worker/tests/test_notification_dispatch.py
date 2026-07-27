"""Notification dispatch: channel routing, consent gating, duplicate
prevention, masking, failure persistence, and delivery callbacks."""

import base64
import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from ai_database.enums import ConsentStatus, NotificationChannel, NotificationType
from ai_providers.errors import ProviderUnavailableError
from ai_providers.messaging import MockEmailProvider, MockSMSProvider, SendResult
from ai_shared.crypto import AesGcmEncryptionService, normalize_phone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from worker import notifications as notify

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:test@localhost:55432/receptionist_test",
)

DATA_KEY = base64.b64encode(b"N" * 32).decode()
HASH_KEY = base64.b64encode(b"M" * 32).decode()


def _db_reachable() -> bool:
    from sqlalchemy.engine import make_url

    url = make_url(TEST_DATABASE_URL)
    try:
        with socket.create_connection((url.host or "localhost", url.port or 5432), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="test database not reachable")

CRYPTO = AesGcmEncryptionService(data_key_b64=DATA_KEY, hash_key_b64=HASH_KEY)
OWNER_EMAIL = "owner@example.com"
OWNER_PHONE = "+15551234821"


class FailingEmail:
    """Always unavailable — exercises the transient-failure path."""

    def __init__(self) -> None:
        self.calls = 0

    async def send_template(
        self, *, to_email: str, template: str, variables: dict[str, str], idempotency_key: str
    ) -> SendResult:
        self.calls += 1
        raise ProviderUnavailableError("resend down", provider="resend")


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
async def tenant(db: AsyncSession) -> dict[str, Any]:
    tenant_id, call_id = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO tenants (id, name, slug, vertical, timezone, status, country) "
            "VALUES (:tid, 'Ace Plumbing', :slug, 'plumbing', 'UTC', 'active', 'US')"
        ),
        {"tid": tenant_id, "slug": f"notif-{uuid.uuid4().hex[:8]}"},
    )
    await db.execute(
        text(
            "INSERT INTO tenant_config (tenant_id, language, recording_enabled, "
            "max_call_seconds, configuration_version, notification_email, "
            "escalation_number, timezone) "
            "VALUES (:tid, 'en', false, 900, 1, :email, :phone, 'UTC')"
        ),
        {"tid": tenant_id, "email": OWNER_EMAIL, "phone": OWNER_PHONE},
    )
    await db.execute(
        text(
            "INSERT INTO calls (id, tenant_id, provider_call_sid, to_number, started_at, "
            "direction, transport, recording_status, recording_legal_hold, "
            "transcript_status, post_processing_status) "
            "VALUES (:cid, :tid, :sid, '+15550002000', now(), 'inbound', 'phone', "
            "'disabled', false, 'pending', 'pending')"
        ),
        {"cid": call_id, "tid": tenant_id, "sid": f"CA_n_{uuid.uuid4().hex[:10]}"},
    )
    return {"tenant_id": tenant_id, "call_id": call_id}


@pytest.fixture(autouse=True)
def providers() -> Iterator[dict[str, Any]]:
    email, sms = MockEmailProvider(), MockSMSProvider()
    notify.set_email_provider(email)
    notify.set_sms_provider(sms)
    yield {"email": email, "sms": sms}
    notify.set_email_provider(None)
    notify.set_sms_provider(None)


async def _grant_consent(db: AsyncSession, tenant_id: uuid.UUID, country: str = "US") -> None:
    await db.execute(
        text(
            "INSERT INTO sms_consents (id, tenant_id, phone_hash, phone_last_four, "
            "country, status, granted_at) "
            "VALUES (:id, :tid, :hash, '4821', :country, 'granted', now())"
        ),
        {
            "id": uuid.uuid4(),
            "tid": tenant_id,
            "hash": CRYPTO.hash_for_lookup(normalize_phone(OWNER_PHONE)),
            "country": country,
        },
    )


async def _deliveries(db: AsyncSession, tenant_id: uuid.UUID) -> list[Any]:
    return list(
        (
            await db.execute(
                text(
                    "SELECT channel, status, recipient_masked, template, attempts, "
                    "failure_category, provider_message_id "
                    "FROM notification_deliveries WHERE tenant_id = :tid ORDER BY channel"
                ),
                {"tid": tenant_id},
            )
        ).all()
    )


async def test_email_sent_and_recorded(db: AsyncSession, tenant: dict[str, Any]) -> None:
    results = await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.NEW_BOOKING,
        variables={"summary": "Booked a drain cleaning.", "time": "Tue 10am"},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
    )
    assert [r.status.value for r in results] == ["sent"]

    rows = await _deliveries(db, tenant["tenant_id"])
    assert len(rows) == 1
    assert rows[0].channel == "email"
    assert rows[0].status == "sent"
    assert rows[0].template == "booking_confirmation"
    assert rows[0].attempts == 1
    assert rows[0].provider_message_id


async def test_recipient_is_stored_masked(db: AsyncSession, tenant: dict[str, Any]) -> None:
    await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.NEW_BOOKING,
        variables={"summary": "s"},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
    )
    rows = await _deliveries(db, tenant["tenant_id"])
    assert rows[0].recipient_masked == "o***@example.com"
    assert OWNER_EMAIL not in rows[0].recipient_masked


async def test_duplicate_dispatch_sends_once(
    db: AsyncSession, tenant: dict[str, Any], providers: dict[str, Any]
) -> None:
    for _ in range(3):
        await notify.dispatch(
            db,
            tenant_id=tenant["tenant_id"],
            notification_type=NotificationType.NEW_BOOKING,
            variables={"summary": "s"},
            crypto=CRYPTO,
            call_id=tenant["call_id"],
        )
    assert len(providers["email"].sent) == 1
    assert len(await _deliveries(db, tenant["tenant_id"])) == 1


async def test_tenant_can_disable_a_channel(
    db: AsyncSession, tenant: dict[str, Any], providers: dict[str, Any]
) -> None:
    await db.execute(
        text(
            "INSERT INTO notification_preferences (id, tenant_id, notification_type, "
            "channel, enabled) VALUES (:id, :tid, 'new_booking', 'email', false)"
        ),
        {"id": uuid.uuid4(), "tid": tenant["tenant_id"]},
    )
    results = await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.NEW_BOOKING,
        variables={"summary": "s"},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
    )
    assert results == []
    assert providers["email"].sent == []


async def test_preference_destination_overrides_config(
    db: AsyncSession, tenant: dict[str, Any], providers: dict[str, Any]
) -> None:
    await db.execute(
        text(
            "INSERT INTO notification_preferences (id, tenant_id, notification_type, "
            "channel, enabled, destination) "
            "VALUES (:id, :tid, 'new_booking', 'email', true, 'dispatch@example.com')"
        ),
        {"id": uuid.uuid4(), "tid": tenant["tenant_id"]},
    )
    await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.NEW_BOOKING,
        variables={"summary": "s"},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
    )
    assert providers["email"].sent[0]["to"] == "dispatch@example.com"


async def test_sms_suppressed_without_consent(
    db: AsyncSession, tenant: dict[str, Any], providers: dict[str, Any]
) -> None:
    results = await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.EMERGENCY_ESCALATION,
        variables={"summary": "Basement flooding."},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
    )
    by_channel = {r.channel: r for r in results}
    assert by_channel[NotificationChannel.EMAIL].status.value == "sent"
    sms = by_channel[NotificationChannel.SMS]
    assert sms.status.value == "suppressed"
    assert sms.reason == "no_consent"
    assert providers["sms"].sent == []

    rows = {r.channel: r for r in await _deliveries(db, tenant["tenant_id"])}
    assert rows["sms"].status == "suppressed"
    assert rows["sms"].failure_category == "no_consent"
    assert rows["sms"].recipient_masked == "···4821"


async def test_sms_sent_with_consent_and_opt_out_language(
    db: AsyncSession, tenant: dict[str, Any], providers: dict[str, Any]
) -> None:
    await _grant_consent(db, tenant["tenant_id"])
    results = await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.EMERGENCY_ESCALATION,
        variables={"summary": "Basement flooding."},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
        now=datetime(2026, 7, 28, 15, 0, tzinfo=UTC),
    )
    sms = next(r for r in results if r.channel is NotificationChannel.SMS)
    assert sms.status.value == "sent"
    assert len(providers["sms"].sent) == 1
    assert "STOP" in providers["sms"].sent[0]["variables"]["opt_out_text"]


async def test_quiet_hours_suppress_routine_sms_but_not_emergency(
    db: AsyncSession, tenant: dict[str, Any]
) -> None:
    await _grant_consent(db, tenant["tenant_id"])
    # 23:00 UTC, tenant timezone UTC — inside the US quiet window.
    late = datetime(2026, 7, 28, 23, 0, tzinfo=UTC)

    await db.execute(
        text(
            "INSERT INTO notification_preferences (id, tenant_id, notification_type, "
            "channel, enabled) VALUES (:id, :tid, 'urgent_message', 'sms', true)"
        ),
        {"id": uuid.uuid4(), "tid": tenant["tenant_id"]},
    )
    routine = await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.URGENT_MESSAGE,
        variables={"summary": "Call back tomorrow."},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
        now=late,
    )
    sms = next(r for r in routine if r.channel is NotificationChannel.SMS)
    assert sms.status.value == "suppressed"
    assert sms.reason == "quiet_hours"

    emergency = await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.EMERGENCY_ESCALATION,
        variables={"summary": "Gas smell."},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
        now=late,
    )
    urgent_sms = next(r for r in emergency if r.channel is NotificationChannel.SMS)
    assert urgent_sms.status.value == "sent"


async def test_uk_number_has_no_quiet_hours(db: AsyncSession, tenant: dict[str, Any]) -> None:
    """US quiet hours must not be applied to a UK recipient."""
    await _grant_consent(db, tenant["tenant_id"], country="GB")
    await db.execute(
        text(
            "INSERT INTO notification_preferences (id, tenant_id, notification_type, "
            "channel, enabled) VALUES (:id, :tid, 'urgent_message', 'sms', true)"
        ),
        {"id": uuid.uuid4(), "tid": tenant["tenant_id"]},
    )
    results = await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.URGENT_MESSAGE,
        variables={"summary": "Call back."},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
        now=datetime(2026, 7, 28, 23, 0, tzinfo=UTC),
    )
    sms = next(r for r in results if r.channel is NotificationChannel.SMS)
    assert sms.status.value == "sent"


async def test_unsubscribed_email_is_suppressed(
    db: AsyncSession, tenant: dict[str, Any], providers: dict[str, Any]
) -> None:
    await db.execute(
        text(
            "INSERT INTO email_suppressions (id, tenant_id, email_hash, reason) "
            "VALUES (:id, :tid, :hash, 'unsubscribed')"
        ),
        {
            "id": uuid.uuid4(),
            "tid": tenant["tenant_id"],
            "hash": CRYPTO.hash_for_lookup(OWNER_EMAIL),
        },
    )
    results = await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.NEW_BOOKING,
        variables={"summary": "s"},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
    )
    assert results[0].status.value == "suppressed"
    assert results[0].reason == "unsubscribed"
    assert providers["email"].sent == []


async def test_provider_failure_is_persisted_as_retryable(
    db: AsyncSession, tenant: dict[str, Any]
) -> None:
    failing = FailingEmail()
    notify.set_email_provider(failing)
    results = await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.FAILED_CALL,
        variables={"summary": "Call dropped."},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
    )
    assert results[0].status.value == "failed"
    assert results[0].reason == "unavailable"

    rows = await _deliveries(db, tenant["tenant_id"])
    assert rows[0].status == "failed"
    assert rows[0].failure_category == "unavailable"
    assert rows[0].attempts == 1


async def test_sensitive_variables_never_reach_a_provider(
    db: AsyncSession, tenant: dict[str, Any], providers: dict[str, Any]
) -> None:
    from ai_domain.notifications import NotificationPolicyError

    with pytest.raises(NotificationPolicyError):
        await notify.dispatch(
            db,
            tenant_id=tenant["tenant_id"],
            notification_type=NotificationType.NEW_BOOKING,
            variables={"transcript": "Caller: my card number is ..."},
            crypto=CRYPTO,
            call_id=tenant["call_id"],
        )
    assert providers["email"].sent == []


async def test_delivery_callback_marks_delivered(db: AsyncSession, tenant: dict[str, Any]) -> None:
    await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.NEW_BOOKING,
        variables={"summary": "s"},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
    )
    rows = await _deliveries(db, tenant["tenant_id"])
    message_id = rows[0].provider_message_id

    updated = await notify.record_delivery_callback(
        db, provider_message_id=message_id, status="delivered"
    )
    assert updated is not None
    assert updated.status.value == "delivered"
    assert updated.delivered_at is not None


async def test_delivery_callback_marks_failure(db: AsyncSession, tenant: dict[str, Any]) -> None:
    await notify.dispatch(
        db,
        tenant_id=tenant["tenant_id"],
        notification_type=NotificationType.NEW_BOOKING,
        variables={"summary": "s"},
        crypto=CRYPTO,
        call_id=tenant["call_id"],
    )
    rows = await _deliveries(db, tenant["tenant_id"])
    updated = await notify.record_delivery_callback(
        db, provider_message_id=rows[0].provider_message_id, status="bounced"
    )
    assert updated is not None
    assert updated.status.value == "failed"
    assert updated.failure_category == "bounced"


async def test_unknown_callback_message_is_ignored(db: AsyncSession) -> None:
    assert (
        await notify.record_delivery_callback(
            db, provider_message_id="SM_unknown", status="delivered"
        )
        is None
    )


def test_consent_status_enum_defaults_to_unknown() -> None:
    """Consent is never assumed — the default is 'not asked'."""
    assert ConsentStatus.UNKNOWN.value == "unknown"
