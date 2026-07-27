"""Google Calendar integration endpoints.

Admin-initiated connect + health under /admin; the OAuth callback is a
public endpoint (the admin's browser arrives from Google) protected by
the signed state parameter instead of a session.
"""

import uuid
from datetime import datetime
from typing import Annotated

import structlog
from ai_database.enums import CalendarConnectionStatus
from ai_database.models import CalendarConnection
from ai_database.repositories import AdminContext
from ai_shared.crypto import AesGcmEncryptionService, EncryptionService
from ai_shared.errors import ValidationFailedError
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import require_platform_admin
from api.db import get_session
from api.services import google_integration
from api.settings import get_settings

logger = structlog.get_logger()

router = APIRouter(tags=["integrations"])


def get_crypto() -> EncryptionService:
    settings = get_settings()
    if not settings.data_encryption_key or not settings.lookup_hash_key:
        raise ValidationFailedError("Encryption keys are not configured.")
    return AesGcmEncryptionService(
        data_key_b64=settings.data_encryption_key,
        hash_key_b64=settings.lookup_hash_key,
    )


def _oauth_settings() -> tuple[str, str, str, str]:
    settings = get_settings()
    if not (
        settings.google_oauth_client_id
        and settings.google_oauth_client_secret
        and settings.google_oauth_redirect_uri
        and settings.call_token_signing_key
    ):
        raise ValidationFailedError("Google OAuth is not configured for this environment.")
    return (
        settings.google_oauth_client_id,
        settings.google_oauth_client_secret,
        settings.google_oauth_redirect_uri,
        settings.call_token_signing_key,
    )


class ConnectResponse(BaseModel):
    authorization_url: str


class ConnectionView(BaseModel):
    status: str
    calendar_id: str | None
    last_verified_at: datetime | None


@router.post("/admin/tenants/{tenant_id}/integrations/google/connect")
async def start_connect(
    tenant_id: uuid.UUID,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
) -> ConnectResponse:
    client_id, _, redirect_uri, signing_key = _oauth_settings()
    url = google_integration.build_authorization_url(
        tenant_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        signing_key=signing_key,
    )
    return ConnectResponse(authorization_url=url)


@router.get("/integrations/google/callback")
async def oauth_callback(
    session: Annotated[AsyncSession, Depends(get_session)],
    state: Annotated[str, Query()],
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    settings = get_settings()
    client_id, client_secret, redirect_uri, signing_key = _oauth_settings()
    dashboard = settings.dashboard_base_url or ""

    if error or not code:
        logger.warning("google_oauth_denied", error=error)
        return RedirectResponse(f"{dashboard}/admin/tenants?google=denied", status_code=302)

    connection = await google_integration.handle_callback(
        session,
        state=state,
        code=code,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        signing_key=signing_key,
        crypto=get_crypto(),
    )
    return RedirectResponse(
        f"{dashboard}/admin/tenants/{connection.tenant_id}/integrations?google=connected",
        status_code=302,
    )


@router.get("/admin/tenants/{tenant_id}/integrations/google")
async def read_connection(
    tenant_id: uuid.UUID,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConnectionView:
    connection = (
        await session.execute(
            select(CalendarConnection).where(CalendarConnection.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if connection is None:
        return ConnectionView(status="not_connected", calendar_id=None, last_verified_at=None)
    return ConnectionView(
        status=connection.status.value,
        calendar_id=connection.external_calendar_id,
        last_verified_at=connection.last_verified_at,
    )


@router.post("/admin/tenants/{tenant_id}/integrations/google/health")
async def run_health_check(
    tenant_id: uuid.UUID,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConnectionView:
    client_id, client_secret, _, _ = _oauth_settings()
    status: CalendarConnectionStatus = await google_integration.check_health(
        session,
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        crypto=get_crypto(),
        context=context,
    )
    connection = (
        await session.execute(
            select(CalendarConnection).where(CalendarConnection.tenant_id == tenant_id)
        )
    ).scalar_one()
    return ConnectionView(
        status=status.value,
        calendar_id=connection.external_calendar_id,
        last_verified_at=connection.last_verified_at,
    )
