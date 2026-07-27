"""Provider webhooks.

The Twilio voice webhook is the front door of every call: it verifies
the signature over the raw request BEFORE parsing, resolves the tenant
from the dialled number only, enforces the platform call-capacity cap,
creates the call record idempotently, and returns TwiML whose stream
URL carries a signed, single-use call token — the only stream metadata
the voice service will trust.
"""

from datetime import UTC, datetime
from typing import Annotated

import structlog
from ai_database.enums import CallTransport, RecordingStatus, TenantStatus
from ai_database.models import Call, PhoneNumber, Tenant, TenantConfig
from ai_providers.twilio import TwilioTelephonyProvider
from ai_shared.call_tokens import mint_call_token
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.settings import get_settings

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

UNAVAILABLE_MESSAGE = (
    "Thank you for calling. This number is not able to take calls right now. "
    "Please try again later."
)
BUSY_MESSAGE = (
    "Thank you for calling. All of our lines are busy at the moment. Please call back shortly."
)

_provider: TwilioTelephonyProvider | None = None


def get_twilio_provider() -> TwilioTelephonyProvider:
    global _provider
    if _provider is None:
        settings = get_settings()
        if not settings.twilio_account_sid or not settings.twilio_auth_token:
            raise RuntimeError("Twilio is not configured.")
        _provider = TwilioTelephonyProvider(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
        )
    return _provider


def reset_twilio_provider() -> None:
    """Test hook."""
    global _provider
    _provider = None


def _twiml(content: str) -> Response:
    return Response(content=content, media_type="application/xml")


@router.post("/twilio/voice")
async def twilio_voice_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    settings = get_settings()
    provider = get_twilio_provider()

    # Signature over the raw form body and the exact public URL.
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}
    public_url = f"{(settings.twilio_webhook_base_url or '').rstrip('/')}/webhooks/twilio/voice"
    signature = request.headers.get("X-Twilio-Signature", "")
    if not provider.verify_webhook(url=public_url, params=params, signature=signature):
        logger.warning(
            "twilio_signature_invalid",
            source_ip=request.client.host if request.client else None,
        )
        return Response(status_code=403)

    inbound = provider.parse_inbound_call(params)

    # Tenant resolution: the dialled number is the only identity source.
    number = (
        await session.execute(
            select(PhoneNumber).where(
                PhoneNumber.e164 == inbound.to_number,
                PhoneNumber.active.is_(True),
                PhoneNumber.voice_enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    if number is None:
        logger.info("call_unknown_number")
        return _twiml(provider.unavailable_twiml(UNAVAILABLE_MESSAGE))

    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == number.tenant_id))
    ).scalar_one()
    if tenant.status not in (TenantStatus.ACTIVE, TenantStatus.TESTING):
        logger.info("call_inactive_tenant", tenant_status=tenant.status.value)
        return _twiml(provider.unavailable_twiml(UNAVAILABLE_MESSAGE))

    # Platform capacity cap: live calls beyond the limit are declined
    # courteously rather than degrading calls already in progress.
    active_calls = (
        await session.execute(
            select(func.count())
            .select_from(Call)
            .where(
                Call.transport == CallTransport.PHONE,
                Call.ended_at.is_(None),
                Call.started_at >= datetime.now(UTC).replace(hour=0, minute=0, second=0),
            )
        )
    ).scalar_one()
    if active_calls >= settings.max_concurrent_calls:
        logger.warning("call_capacity_reached", active=active_calls)
        return _twiml(provider.unavailable_twiml(BUSY_MESSAGE))

    config = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant.id))
    ).scalar_one_or_none()

    # Idempotent call record: Twilio retries deliver the same CallSid.
    from ai_shared.crypto import last_four, normalize_phone

    call = Call(
        tenant_id=tenant.id,
        phone_number_id=number.id,
        provider_call_sid=inbound.provider_call_sid,
        to_number=inbound.to_number,
        from_number_last_four=last_four(normalize_phone(inbound.from_number)),
        transport=CallTransport.PHONE,
        started_at=datetime.now(UTC),
        recording_status=(
            RecordingStatus.IN_PROGRESS
            if config and config.recording_enabled
            else RecordingStatus.DISABLED
        ),
    )
    try:
        async with session.begin_nested():
            session.add(call)
    except IntegrityError:
        # Retry of the same webhook — reuse the existing record.
        call = (
            await session.execute(
                select(Call).where(Call.provider_call_sid == inbound.provider_call_sid)
            )
        ).scalar_one()
        logger.info("call_webhook_retry", call_id=str(call.id))

    token = mint_call_token(
        call_sid=inbound.provider_call_sid,
        tenant_id=str(tenant.id),
        signing_key=settings.call_token_signing_key or "",
    )
    ws_url = f"{(settings.voice_ws_base_url or '').rstrip('/')}/ws?token={token}"

    announcement = None
    if config and config.recording_enabled and config.recording_consent_text:
        announcement = config.recording_consent_text

    logger.info(
        "call_accepted",
        call_id=str(call.id),
        tenant_id=str(tenant.id),
    )
    return _twiml(provider.stream_response_twiml(ws_url=ws_url, announcement=announcement))
