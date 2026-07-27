"""Google Calendar OAuth integration.

Flow: admin requests a connect URL (tenant-bound signed state) → Google
consent → callback validates state, exchanges the code, selects the
primary calendar, and stores tokens encrypted in calendar_connections.
Health checks run the real provider against stored credentials and
downgrade the connection status on revocation.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from ai_database.audit import write_audit
from ai_database.enums import CalendarConnectionStatus
from ai_database.models import CalendarConnection
from ai_database.repositories import AdminContext
from ai_providers.errors import CredentialRevokedError, ProviderError
from ai_providers.google_calendar import GoogleCalendarAuth, GoogleCalendarProvider
from ai_shared.crypto import EncryptionService
from ai_shared.errors import NotFoundError, ValidationFailedError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — public endpoint
SCOPES = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.readonly"
STATE_TTL_SECONDS = 600


class OAuthStateError(ValidationFailedError):
    pass


def _sign(payload: bytes, key: str) -> str:
    return hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()


def build_state(tenant_id: uuid.UUID, *, signing_key: str) -> str:
    body = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "expires": int(time.time()) + STATE_TTL_SECONDS,
            "nonce": uuid.uuid4().hex,
        }
    ).encode()
    encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
    return f"{encoded}.{_sign(body, signing_key)}"


def parse_state(state: str, *, signing_key: str) -> uuid.UUID:
    try:
        encoded, signature = state.rsplit(".", 1)
        body = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, TypeError) as exc:
        raise OAuthStateError("Malformed state parameter.") from exc
    if not hmac.compare_digest(_sign(body, signing_key), signature):
        raise OAuthStateError("State signature mismatch.")
    payload = json.loads(body)
    if int(payload.get("expires", 0)) < time.time():
        raise OAuthStateError("State expired — restart the connection flow.")
    return uuid.UUID(payload["tenant_id"])


def build_authorization_url(
    tenant_id: uuid.UUID,
    *,
    client_id: str,
    redirect_uri: str,
    signing_key: str,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",  # ensure a refresh token on reconnects
        "state": build_state(tenant_id, signing_key=signing_key),
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def handle_callback(
    session: AsyncSession,
    *,
    state: str,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    signing_key: str,
    crypto: EncryptionService,
    http: httpx.AsyncClient | None = None,
) -> CalendarConnection:
    tenant_id = parse_state(state, signing_key=signing_key)

    client = http or httpx.AsyncClient(timeout=10.0)
    response = await client.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    if response.status_code != 200:
        logger.warning("google_code_exchange_failed", status=response.status_code)
        raise ValidationFailedError("Google authorization failed — try connecting again.")
    tokens: dict[str, Any] = response.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token or not refresh_token:
        raise ValidationFailedError(
            "Google did not grant offline access — remove the app's access in your "
            "Google account and try again."
        )
    expires_at = datetime.now(UTC) + timedelta(seconds=int(tokens.get("expires_in", 3600)))

    # Primary calendar (calendar selection can be changed afterwards).
    calendars = await client.get(
        "https://www.googleapis.com/calendar/v3/users/me/calendarList",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    calendar_id = "primary"
    if calendars.status_code == 200:
        for item in calendars.json().get("items", []):
            if item.get("primary"):
                calendar_id = item.get("id", "primary")
                break

    connection = (
        await session.execute(
            select(CalendarConnection).where(CalendarConnection.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if connection is None:
        connection = CalendarConnection(tenant_id=tenant_id, provider="google")
        session.add(connection)

    connection.external_calendar_id = calendar_id
    connection.encrypted_access_token = crypto.encrypt(access_token)
    connection.encrypted_refresh_token = crypto.encrypt(refresh_token)
    connection.token_expires_at = expires_at
    connection.status = CalendarConnectionStatus.CONNECTED
    connection.last_verified_at = datetime.now(UTC)
    await session.flush()

    await write_audit(
        session,
        action="integration.google_connected",
        actor_external_user_id=None,
        actor_role="oauth_callback",
        tenant_id=tenant_id,
        resource_type="calendar_connection",
        resource_id=str(connection.id),
        after={"calendar_id": calendar_id},
    )
    logger.info("google_calendar_connected", tenant_id=str(tenant_id))
    return connection


async def build_provider(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    client_id: str,
    client_secret: str,
    crypto: EncryptionService,
    http: httpx.AsyncClient | None = None,
) -> tuple[GoogleCalendarProvider, CalendarConnection]:
    connection = (
        await session.execute(
            select(CalendarConnection).where(CalendarConnection.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if (
        connection is None
        or not connection.encrypted_access_token
        or not connection.encrypted_refresh_token
    ):
        raise NotFoundError("No Google Calendar connection for this tenant.")

    async def save_refreshed(access_token: str, expires_at: datetime) -> None:
        connection.encrypted_access_token = crypto.encrypt(access_token)
        connection.token_expires_at = expires_at
        await session.flush()

    auth = GoogleCalendarAuth(
        client_id=client_id,
        client_secret=client_secret,
        access_token=crypto.decrypt(connection.encrypted_access_token),
        refresh_token=crypto.decrypt(connection.encrypted_refresh_token),
        token_expires_at=connection.token_expires_at,
        on_token_refreshed=save_refreshed,
        http=http,
    )
    provider = GoogleCalendarProvider(
        auth=auth,
        calendar_id=connection.external_calendar_id or "primary",
        http=http,
    )
    return provider, connection


async def check_health(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    client_id: str,
    client_secret: str,
    crypto: EncryptionService,
    context: AdminContext,
    http: httpx.AsyncClient | None = None,
) -> CalendarConnectionStatus:
    provider, connection = await build_provider(
        session,
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        crypto=crypto,
        http=http,
    )
    try:
        await provider.validate_connection()
        connection.status = CalendarConnectionStatus.CONNECTED
        connection.last_verified_at = datetime.now(UTC)
    except CredentialRevokedError:
        connection.status = CalendarConnectionStatus.REVOKED
    except ProviderError:
        connection.status = CalendarConnectionStatus.ERROR
    await session.flush()

    await write_audit(
        session,
        action="integration.google_health_checked",
        actor_external_user_id=context.actor_external_user_id,
        actor_role="platform_admin",
        tenant_id=tenant_id,
        resource_type="calendar_connection",
        resource_id=str(connection.id),
        after={"status": connection.status.value},
        request_id=context.request_id,
    )
    return connection.status
