"""Worker service entrypoint.

Job endpoints are invoked exclusively by QStash; every request is
signature-verified before any parsing, and handlers are idempotent so
at-least-once delivery is safe.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from ai_shared.crypto import AesGcmEncryptionService, EncryptionService
from ai_shared.errors import NotFoundError, UnauthorizedError, ValidationFailedError
from ai_shared.fastapi_setup import configure_service_app
from ai_telemetry import configure_logging
from fastapi import FastAPI, Request
from pydantic import BaseModel

from worker.settings import get_settings

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(
        service_name=settings.service_name,
        log_level=settings.log_level,
        json_output=settings.environment.value != "local",
    )
    logger.info("service_starting", environment=settings.environment.value)
    yield
    from worker.db import dispose_engine

    await dispose_engine()
    logger.info("service_stopping")


class JobResult(BaseModel):
    status: str


def _optional_crypto() -> EncryptionService | None:
    """None when keys are unconfigured — notifications are then skipped
    rather than sent unencrypted or crashing the job."""
    settings = get_settings()
    if not settings.data_encryption_key or not settings.lookup_hash_key:
        return None
    return AesGcmEncryptionService(
        data_key_b64=settings.data_encryption_key,
        hash_key_b64=settings.lookup_hash_key,
    )


async def _verify_delivery(request: Request) -> bytes:
    """QStash signature check over the raw body; 401 on any failure."""
    from worker.qstash import QStashVerificationError, verify_qstash_signature

    settings = get_settings()
    if not settings.qstash_current_signing_key:
        raise UnauthorizedError("Job delivery is not configured.")
    body = await request.body()
    token = request.headers.get("Upstash-Signature", "")
    url = f"{(settings.worker_base_url or '').rstrip('/')}{request.url.path}"
    try:
        verify_qstash_signature(
            token,
            url=url,
            body=body,
            current_key=settings.qstash_current_signing_key,
            next_key=settings.qstash_next_signing_key,
        )
    except QStashVerificationError as exc:
        logger.warning("qstash_signature_invalid", reason=str(exc))
        raise UnauthorizedError("Invalid delivery signature.") from exc
    return body


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Receptionist Worker",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.post("/jobs/post-call")
    async def post_call_job(request: Request) -> JobResult:
        import json

        from worker.db import get_session_factory
        from worker.llm import get_llm
        from worker.post_call import process_call

        body = await _verify_delivery(request)
        try:
            payload = json.loads(body)
            call_id = uuid.UUID(payload["call_id"])
        except (ValueError, KeyError) as exc:
            raise ValidationFailedError("Malformed job payload.") from exc

        factory = get_session_factory()
        if factory is None:
            raise ValidationFailedError("Worker database is not configured.")
        try:
            async with factory() as session, session.begin():
                status = await process_call(
                    session,
                    call_id=call_id,
                    llm=get_llm(),
                    crypto=_optional_crypto(),
                )
        except LookupError as exc:
            raise NotFoundError("Unknown call.") from exc
        return JobResult(status=status.value)

    @app.post("/jobs/retention-sweep")
    async def retention_sweep_job(request: Request) -> JobResult:
        from worker.db import get_session_factory
        from worker.recordings import build_r2_storage, retention_sweep

        await _verify_delivery(request)
        factory = get_session_factory()
        if factory is None:
            raise ValidationFailedError("Worker database is not configured.")
        storage = build_r2_storage(get_settings())
        async with factory() as session, session.begin():
            deleted = await retention_sweep(session, storage=storage)
        return JobResult(status=f"deleted:{deleted}")

    @app.post("/webhooks/sms-status")
    async def sms_status_callback(request: Request) -> JobResult:
        """Twilio delivery callback.

        Signature-verified like every other Twilio webhook; an unknown
        message id is acknowledged rather than retried forever.
        """
        from ai_providers.twilio import TwilioTelephonyProvider

        from worker.db import get_session_factory
        from worker.notifications import record_delivery_callback

        settings = get_settings()
        if not (settings.twilio_account_sid and settings.twilio_auth_token):
            raise UnauthorizedError("SMS callbacks are not configured.")

        form = await request.form()
        params = {key: str(value) for key, value in form.items()}
        provider = TwilioTelephonyProvider(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
        )
        url = settings.sms_status_callback_url or str(request.url)
        if not provider.verify_webhook(
            url=url, params=params, signature=request.headers.get("X-Twilio-Signature", "")
        ):
            logger.warning("sms_callback_signature_invalid")
            raise UnauthorizedError("Invalid webhook signature.")

        message_sid = params.get("MessageSid") or params.get("SmsSid")
        status = params.get("MessageStatus") or params.get("SmsStatus")
        if not message_sid or not status:
            raise ValidationFailedError("Malformed delivery callback.")

        factory = get_session_factory()
        if factory is None:
            raise ValidationFailedError("Worker database is not configured.")
        async with factory() as session, session.begin():
            delivery = await record_delivery_callback(
                session, provider_message_id=message_sid, status=status
            )
        return JobResult(status=delivery.status.value if delivery else "unknown")

    return configure_service_app(app, service_name="worker")


app = create_app()
