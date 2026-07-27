"""Google integration tests: OAuth state signing, callback token
storage (encrypted), health checks with revocation downgrade."""

import base64
import os
import uuid
from typing import Any

import httpx
import pytest
from ai_database.enums import CalendarConnectionStatus
from ai_database.repositories import AdminContext
from ai_shared.crypto import AesGcmEncryptionService
from ai_shared.errors import ValidationFailedError
from api.services import google_integration
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tests_markers import TEST_DATABASE_URL, requires_db

pytestmark = requires_db

SIGNING_KEY = "test-signing-key"


def _crypto() -> AesGcmEncryptionService:
    return AesGcmEncryptionService(
        data_key_b64=base64.b64encode(os.urandom(32)).decode(),
        hash_key_b64=base64.b64encode(os.urandom(32)).decode(),
    )


# --- state parameter ---------------------------------------------------------


def test_state_roundtrip() -> None:
    tenant_id = uuid.uuid4()
    state = google_integration.build_state(tenant_id, signing_key=SIGNING_KEY)
    assert google_integration.parse_state(state, signing_key=SIGNING_KEY) == tenant_id


def test_state_tamper_rejected() -> None:
    tenant_id = uuid.uuid4()
    state = google_integration.build_state(tenant_id, signing_key=SIGNING_KEY)
    encoded, signature = state.rsplit(".", 1)
    tampered = f"{encoded}x.{signature}"
    with pytest.raises(ValidationFailedError):
        google_integration.parse_state(tampered, signing_key=SIGNING_KEY)


def test_state_wrong_key_rejected() -> None:
    state = google_integration.build_state(uuid.uuid4(), signing_key=SIGNING_KEY)
    with pytest.raises(ValidationFailedError):
        google_integration.parse_state(state, signing_key="other-key")


def test_authorization_url_contains_offline_access() -> None:
    url = google_integration.build_authorization_url(
        uuid.uuid4(),
        client_id="cid",
        redirect_uri="https://api.example/integrations/google/callback",
        signing_key=SIGNING_KEY,
    )
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=" in url


# --- callback ----------------------------------------------------------------


@pytest.fixture
async def db() -> Any:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        yield session
        await session.close()
        await trans.rollback()
    await engine.dispose()


@pytest.fixture
async def tenant_id(db: AsyncSession) -> uuid.UUID:
    tid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO tenants (id, name, slug, vertical, timezone, status) "
            "VALUES (:id, 'GCal Test', :slug, 'plumbing', 'UTC', 'testing')"
        ),
        {"id": tid, "slug": f"gcal-{uuid.uuid4().hex[:8]}"},
    )
    return tid


def _google_http(*, revoked: bool = False) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "access-xyz",
                    "refresh_token": "refresh-xyz",
                    "expires_in": 3600,
                },
            )
        if "calendarList" in url:
            return httpx.Response(
                200,
                json={"items": [{"id": "owner@example.com", "primary": True}]},
            )
        if "/calendars/" in url:
            if revoked:
                return httpx.Response(403, json={"error": "forbidden"})
            return httpx.Response(200, json={"id": "owner@example.com"})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_callback_stores_encrypted_tokens(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    crypto = _crypto()
    state = google_integration.build_state(tenant_id, signing_key=SIGNING_KEY)
    connection = await google_integration.handle_callback(
        db,
        state=state,
        code="auth-code",
        client_id="cid",
        client_secret="cs",
        redirect_uri="https://api.example/cb",
        signing_key=SIGNING_KEY,
        crypto=crypto,
        http=_google_http(),
    )
    assert connection.status is CalendarConnectionStatus.CONNECTED
    assert connection.external_calendar_id == "owner@example.com"
    # Tokens are encrypted at rest and decrypt to the originals.
    assert connection.encrypted_access_token is not None
    assert connection.encrypted_refresh_token is not None
    assert connection.encrypted_access_token != "access-xyz"
    assert crypto.decrypt(connection.encrypted_access_token) == "access-xyz"
    assert crypto.decrypt(connection.encrypted_refresh_token) == "refresh-xyz"


async def test_callback_reconnect_updates_existing_row(
    db: AsyncSession, tenant_id: uuid.UUID
) -> None:
    crypto = _crypto()
    for _ in range(2):
        state = google_integration.build_state(tenant_id, signing_key=SIGNING_KEY)
        await google_integration.handle_callback(
            db,
            state=state,
            code="auth-code",
            client_id="cid",
            client_secret="cs",
            redirect_uri="https://api.example/cb",
            signing_key=SIGNING_KEY,
            crypto=crypto,
            http=_google_http(),
        )
    count = (
        await db.execute(
            text("SELECT count(*) FROM calendar_connections WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
    ).scalar_one()
    assert count == 1


async def test_health_check_connected_and_revoked(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    crypto = _crypto()
    state = google_integration.build_state(tenant_id, signing_key=SIGNING_KEY)
    await google_integration.handle_callback(
        db,
        state=state,
        code="auth-code",
        client_id="cid",
        client_secret="cs",
        redirect_uri="https://api.example/cb",
        signing_key=SIGNING_KEY,
        crypto=crypto,
        http=_google_http(),
    )
    context = AdminContext(actor_external_user_id="admin_user")

    healthy = await google_integration.check_health(
        db,
        tenant_id=tenant_id,
        client_id="cid",
        client_secret="cs",
        crypto=crypto,
        context=context,
        http=_google_http(),
    )
    assert healthy is CalendarConnectionStatus.CONNECTED

    revoked = await google_integration.check_health(
        db,
        tenant_id=tenant_id,
        client_id="cid",
        client_secret="cs",
        crypto=crypto,
        context=context,
        http=_google_http(revoked=True),
    )
    assert revoked is CalendarConnectionStatus.REVOKED
