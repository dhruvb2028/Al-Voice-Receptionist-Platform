"""Twilio voice-webhook tests: signature verification, tenant
resolution by number, safe rejections, idempotent call records,
capacity cap, and token-bearing TwiML."""

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from ai_providers.twilio import compute_twilio_signature
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tests_markers import requires_db

pytestmark = requires_db

AUTH_TOKEN = "twilio-test-auth-token"  # noqa: S105 — test fixture
SIGNING_KEY = "call-token-signing-key"
WEBHOOK_BASE = "https://api.staging.example"
WEBHOOK_URL = f"{WEBHOOK_BASE}/webhooks/twilio/voice"


@pytest.fixture
async def webhook_app(migrated_database: str) -> AsyncIterator[Any]:
    from api.db import get_session
    from api.main import create_app
    from api.routers.webhooks import reset_twilio_provider
    from api.settings import get_settings

    get_settings.cache_clear()
    os.environ.update(
        {
            "DATABASE_URL": migrated_database,
            "TWILIO_ACCOUNT_SID": "AC_test",
            "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
            "TWILIO_WEBHOOK_BASE_URL": WEBHOOK_BASE,
            "VOICE_WS_BASE_URL": "wss://voice.staging.example",
            "CALL_TOKEN_SIGNING_KEY": SIGNING_KEY,
        }
    )
    get_settings.cache_clear()
    reset_twilio_provider()
    app = create_app()

    engine = create_async_engine(migrated_database)

    async def _test_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = _test_session
    try:
        yield app
    finally:
        for key in (
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "TWILIO_WEBHOOK_BASE_URL",
            "VOICE_WS_BASE_URL",
            "CALL_TOKEN_SIGNING_KEY",
        ):
            os.environ.pop(key, None)
        get_settings.cache_clear()
        reset_twilio_provider()
        await engine.dispose()


@pytest.fixture
async def phone_tenant(migrated_database: str) -> AsyncIterator[dict[str, Any]]:
    """Active tenant with an assigned number (+ a paused one)."""
    engine = create_async_engine(migrated_database)
    suffix = uuid.uuid4().hex[:6]
    digits = str(uuid.uuid4().int)[:4]
    active_id, paused_id = uuid.uuid4(), uuid.uuid4()
    active_number = f"+1555200{digits}"
    paused_number = f"+1555201{digits}"

    async with AsyncSession(engine) as session, session.begin():
        for tid, slug, status in (
            (active_id, f"tw-active-{suffix}", "active"),
            (paused_id, f"tw-paused-{suffix}", "paused"),
        ):
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, vertical, timezone, status) "
                    "VALUES (:id, :name, :slug, 'plumbing', 'UTC', :status)"
                ),
                {"id": tid, "name": f"TW {slug}", "slug": slug, "status": status},
            )
        await session.execute(
            text(
                "INSERT INTO tenant_config (tenant_id, recording_enabled, "
                "recording_consent_text, max_call_seconds, language, configuration_version) "
                "VALUES (:tid, true, 'This call may be recorded.', 900, 'en', 1)"
            ),
            {"tid": active_id},
        )
        for tid, number in ((active_id, active_number), (paused_id, paused_number)):
            await session.execute(
                text(
                    "INSERT INTO phone_numbers (id, tenant_id, e164, provider, active, "
                    "voice_enabled, sms_enabled) "
                    "VALUES (:id, :tid, :e164, 'twilio', true, true, false)"
                ),
                {"id": uuid.uuid4(), "tid": tid, "e164": number},
            )

    yield {
        "active_tenant_id": active_id,
        "active_number": active_number,
        "paused_number": paused_number,
    }

    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            text("DELETE FROM calls WHERE tenant_id IN (:a, :b)"),
            {"a": active_id, "b": paused_id},
        )
        # Numbers and calls RESTRICT tenant deletion by design.
        await session.execute(
            text("DELETE FROM phone_numbers WHERE tenant_id IN (:a, :b)"),
            {"a": active_id, "b": paused_id},
        )
        await session.execute(
            text("DELETE FROM tenants WHERE id IN (:a, :b)"),
            {"a": active_id, "b": paused_id},
        )
    await engine.dispose()


def _signed_post(
    params: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    signature = compute_twilio_signature(auth_token=AUTH_TOKEN, url=WEBHOOK_URL, params=params)
    return params, {"X-Twilio-Signature": signature}


def _call_params(to_number: str, call_sid: str | None = None) -> dict[str, str]:
    return {
        "CallSid": call_sid or f"CA{uuid.uuid4().hex}",
        "From": "+15550009999",
        "To": to_number,
        "Direction": "inbound",
    }


async def _post(app: Any, params: dict[str, str], headers: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/webhooks/twilio/voice", data=params, headers=headers)


async def test_invalid_signature_rejected_no_side_effects(
    webhook_app: Any, phone_tenant: dict[str, Any], migrated_database: str
) -> None:
    params = _call_params(phone_tenant["active_number"])
    response = await _post(webhook_app, params, {"X-Twilio-Signature": "forged"})
    assert response.status_code == 403

    engine = create_async_engine(migrated_database)
    async with AsyncSession(engine) as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM calls WHERE provider_call_sid = :sid"),
                {"sid": params["CallSid"]},
            )
        ).scalar_one()
    await engine.dispose()
    assert count == 0


async def test_missing_signature_rejected(webhook_app: Any, phone_tenant: dict[str, Any]) -> None:
    response = await _post(webhook_app, _call_params(phone_tenant["active_number"]), {})
    assert response.status_code == 403


async def test_unknown_number_gets_safe_twiml(webhook_app: Any) -> None:
    params, headers = _signed_post(_call_params("+15559990000"))
    response = await _post(webhook_app, params, headers)
    assert response.status_code == 200
    assert "<Hangup/>" in response.text
    assert "wss://" not in response.text


async def test_paused_tenant_gets_safe_twiml(
    webhook_app: Any, phone_tenant: dict[str, Any]
) -> None:
    params, headers = _signed_post(_call_params(phone_tenant["paused_number"]))
    response = await _post(webhook_app, params, headers)
    assert response.status_code == 200
    assert "<Hangup/>" in response.text


async def test_active_tenant_gets_stream_twiml_with_token(
    webhook_app: Any, phone_tenant: dict[str, Any], migrated_database: str
) -> None:
    from ai_shared.call_tokens import verify_call_token

    params, headers = _signed_post(_call_params(phone_tenant["active_number"]))
    response = await _post(webhook_app, params, headers)
    assert response.status_code == 200
    body = response.text
    # Recording announcement precedes the stream.
    assert "<Say>This call may be recorded.</Say>" in body
    assert "<Connect><Stream" in body

    # The stream URL carries a valid signed token bound to this call.
    import re

    match = re.search(r'url="wss://voice\.staging\.example/ws\?token=([^"]+)"', body)
    assert match, body
    claims = verify_call_token(match.group(1), signing_key=SIGNING_KEY)
    assert claims["call_sid"] == params["CallSid"]
    assert claims["tenant_id"] == str(phone_tenant["active_tenant_id"])

    # Call record created with redacted caller data only.
    engine = create_async_engine(migrated_database)
    async with AsyncSession(engine) as session:
        row = (
            await session.execute(
                text(
                    "SELECT from_number_last_four, transport FROM calls "
                    "WHERE provider_call_sid = :sid"
                ),
                {"sid": params["CallSid"]},
            )
        ).one()
    await engine.dispose()
    assert row[0] == "9999"
    assert row[1] == "phone"


async def test_webhook_retry_is_idempotent(
    webhook_app: Any, phone_tenant: dict[str, Any], migrated_database: str
) -> None:
    params, headers = _signed_post(_call_params(phone_tenant["active_number"]))
    first = await _post(webhook_app, params, headers)
    second = await _post(webhook_app, params, headers)
    assert first.status_code == second.status_code == 200

    engine = create_async_engine(migrated_database)
    async with AsyncSession(engine) as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM calls WHERE provider_call_sid = :sid"),
                {"sid": params["CallSid"]},
            )
        ).scalar_one()
    await engine.dispose()
    assert count == 1


async def test_capacity_cap_declines_courteously(
    webhook_app: Any, phone_tenant: dict[str, Any], migrated_database: str
) -> None:
    # Fill the platform with active phone calls.
    engine = create_async_engine(migrated_database)
    sids = [f"CAcap{uuid.uuid4().hex[:10]}" for _ in range(6)]
    async with AsyncSession(engine) as session, session.begin():
        for sid in sids:
            await session.execute(
                text(
                    "INSERT INTO calls (id, tenant_id, provider_call_sid, to_number, "
                    "started_at, direction, transport, recording_status, "
                    "transcript_status, post_processing_status) "
                    "VALUES (:id, :tid, :sid, :to_number, now(), 'inbound', 'phone', "
                    "'disabled', 'pending', 'pending')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tid": phone_tenant["active_tenant_id"],
                    "sid": sid,
                    "to_number": phone_tenant["active_number"],
                },
            )
    try:
        params, headers = _signed_post(_call_params(phone_tenant["active_number"]))
        response = await _post(webhook_app, params, headers)
        assert response.status_code == 200
        assert "busy" in response.text.lower()
        assert "wss://" not in response.text
    finally:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                text("DELETE FROM calls WHERE provider_call_sid = ANY(:sids)"),
                {"sids": sids},
            )
        await engine.dispose()
