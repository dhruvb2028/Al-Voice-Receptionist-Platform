"""Notification dispatch: preferences → policy → provider → delivery row.

Every send goes through :func:`dispatch`, which is idempotent by
construction. The delivery row is inserted *before* the provider call
using a unique idempotency key, so a redelivered job conflicts on the
insert and returns the original outcome instead of sending twice.

Suppression is a first-class result, not an error: a recipient who never
granted SMS consent, or who unsubscribed from email, produces a
``SUPPRESSED`` delivery row that explains itself in the dashboard.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from ai_database.enums import (
    ConsentStatus,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
)
from ai_database.models import (
    EmailSuppression,
    NotificationDelivery,
    NotificationPreference,
    SmsConsent,
    Tenant,
    TenantConfig,
)
from ai_domain.notifications import (
    NotificationPolicyError,
    NotificationTemplate,
    TenantBranding,
    assert_approved_template,
    assert_safe_variables,
    evaluate_sms,
    sms_policy_for_country,
)
from ai_providers.errors import DuplicateSendError, ProviderError
from ai_providers.messaging import EmailProvider, SMSProvider
from ai_shared.crypto import EncryptionService, last_four, normalize_phone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

#: Which channel each event uses when a tenant has expressed no preference.
DEFAULT_CHANNELS: dict[NotificationType, tuple[NotificationChannel, ...]] = {
    NotificationType.NEW_BOOKING: (NotificationChannel.EMAIL,),
    NotificationType.EMERGENCY_ESCALATION: (
        NotificationChannel.EMAIL,
        NotificationChannel.SMS,
    ),
    NotificationType.URGENT_MESSAGE: (NotificationChannel.EMAIL,),
    NotificationType.FAILED_CALL: (NotificationChannel.EMAIL,),
    NotificationType.DAILY_SUMMARY: (NotificationChannel.EMAIL,),
    NotificationType.WEEKLY_REPORT: (NotificationChannel.EMAIL,),
    NotificationType.CALENDAR_DISCONNECTED: (NotificationChannel.EMAIL,),
}

_EMAIL_TEMPLATES: dict[NotificationType, NotificationTemplate] = {
    NotificationType.NEW_BOOKING: NotificationTemplate.BOOKING_CONFIRMATION,
    NotificationType.EMERGENCY_ESCALATION: NotificationTemplate.URGENT_ESCALATION,
    NotificationType.URGENT_MESSAGE: NotificationTemplate.NEW_MESSAGE,
    NotificationType.FAILED_CALL: NotificationTemplate.FAILED_CALL_ALERT,
    NotificationType.DAILY_SUMMARY: NotificationTemplate.DAILY_SUMMARY,
    NotificationType.WEEKLY_REPORT: NotificationTemplate.WEEKLY_REPORT,
    NotificationType.CALENDAR_DISCONNECTED: NotificationTemplate.CALENDAR_DISCONNECTED,
}

_SMS_TEMPLATES: dict[NotificationType, NotificationTemplate] = {
    NotificationType.NEW_BOOKING: NotificationTemplate.SMS_NEW_BOOKING,
    NotificationType.URGENT_MESSAGE: NotificationTemplate.SMS_URGENT_MESSAGE,
    NotificationType.EMERGENCY_ESCALATION: NotificationTemplate.SMS_EMERGENCY,
}

_EMERGENCY_TYPES = frozenset({NotificationType.EMERGENCY_ESCALATION})

# Provider-side singletons, wired at startup and swapped in tests.
_email: EmailProvider | None = None
_sms: SMSProvider | None = None


def get_email_provider() -> EmailProvider | None:
    return _email


def set_email_provider(provider: EmailProvider | None) -> None:
    global _email
    _email = provider


def get_sms_provider() -> SMSProvider | None:
    return _sms


def set_sms_provider(provider: SMSProvider | None) -> None:
    global _sms
    _sms = provider


def mask_email(address: str) -> str:
    """d***@example.com — enough to recognise, not enough to reuse."""
    local, _, domain = address.partition("@")
    if not domain:
        return "***"
    head = local[0] if local else ""
    return f"{head}***@{domain}"


def mask_phone(e164: str) -> str:
    return f"···{last_four(e164)}"


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _country_from_e164(number: str) -> str:
    """Rough dial-code → country mapping.

    Deliberately conservative: anything unrecognised returns ``ZZ``, which
    maps to the strictest SMS policy rather than to US rules.
    """
    digits = normalize_phone(number)
    if digits.startswith("+1"):
        return "US"
    if digits.startswith("+44"):
        return "GB"
    if digits.startswith("+61"):
        return "AU"
    return "ZZ"


@dataclass(frozen=True)
class DispatchResult:
    channel: NotificationChannel
    status: NotificationStatus
    reason: str = ""
    provider_message_id: str | None = None


async def _preferences(
    session: AsyncSession, tenant_id: uuid.UUID, notification_type: NotificationType
) -> dict[NotificationChannel, NotificationPreference]:
    rows = (
        (
            await session.execute(
                select(NotificationPreference).where(
                    NotificationPreference.tenant_id == tenant_id,
                    NotificationPreference.notification_type == notification_type,
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.channel: row for row in rows}


async def resolve_channels(
    session: AsyncSession, tenant_id: uuid.UUID, notification_type: NotificationType
) -> list[tuple[NotificationChannel, str | None]]:
    """Enabled channels and their destination overrides, in send order."""
    prefs = await _preferences(session, tenant_id, notification_type)
    channels: list[tuple[NotificationChannel, str | None]] = []
    for channel in DEFAULT_CHANNELS.get(notification_type, ()):
        pref = prefs.get(channel)
        if pref is not None and not pref.enabled:
            continue
        channels.append((channel, pref.destination if pref else None))
    # A tenant can also opt *in* to a channel that is not a default.
    for channel, pref in prefs.items():
        if pref.enabled and all(channel != existing for existing, _ in channels):
            channels.append((channel, pref.destination))
    return channels


async def _branding(session: AsyncSession, tenant_id: uuid.UUID) -> TenantBranding:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    config = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    ).scalar_one_or_none()
    return TenantBranding(
        business_name=tenant.name if tenant else "Your business",
        website=config.website if config else None,
        support_phone=config.business_phone if config else None,
    )


async def _email_suppressed(
    session: AsyncSession, tenant_id: uuid.UUID, address: str, crypto: EncryptionService
) -> bool:
    digest = crypto.hash_for_lookup(address.strip().lower())
    found = (
        await session.execute(
            select(EmailSuppression.id).where(
                EmailSuppression.tenant_id == tenant_id,
                EmailSuppression.email_hash == digest,
            )
        )
    ).scalar_one_or_none()
    return found is not None


async def _sms_consent(
    session: AsyncSession, tenant_id: uuid.UUID, number: str, crypto: EncryptionService
) -> SmsConsent | None:
    digest = crypto.hash_for_lookup(normalize_phone(number))
    return (
        await session.execute(
            select(SmsConsent).where(
                SmsConsent.tenant_id == tenant_id, SmsConsent.phone_hash == digest
            )
        )
    ).scalar_one_or_none()


async def _claim(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID | None,
    notification_type: NotificationType,
    channel: NotificationChannel,
    template: str,
    recipient_masked: str,
    idempotency_key: str,
) -> NotificationDelivery | None:
    """Insert the delivery row, or None when this send already happened.

    The unique key does the duplicate-prevention, so two workers racing
    on the same job cannot both reach the provider.
    """
    existing = (
        await session.execute(
            select(NotificationDelivery).where(
                NotificationDelivery.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    delivery = NotificationDelivery(
        tenant_id=tenant_id,
        call_id=call_id,
        notification_type=notification_type,
        channel=channel,
        template=template,
        recipient_masked=recipient_masked,
        status=NotificationStatus.PENDING,
        idempotency_key=idempotency_key,
    )
    session.add(delivery)
    try:
        await session.flush()
    except IntegrityError:
        # Lost the race — the winner's row stands.
        await session.rollback()
        return None
    return delivery


async def dispatch(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    notification_type: NotificationType,
    variables: dict[str, str],
    crypto: EncryptionService,
    call_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    now: datetime | None = None,
) -> list[DispatchResult]:
    """Send one notification across every channel the tenant allows."""
    now = now or datetime.now(UTC)
    assert_safe_variables(variables)

    branding = await _branding(session, tenant_id)
    merged = {**branding.as_variables(), **variables}
    config = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    ).scalar_one_or_none()
    scope = dedupe_key or (str(call_id) if call_id else now.strftime("%Y%m%dT%H"))

    results: list[DispatchResult] = []
    for channel, override in await resolve_channels(session, tenant_id, notification_type):
        if channel is NotificationChannel.EMAIL:
            result = await _dispatch_email(
                session,
                tenant_id=tenant_id,
                call_id=call_id,
                notification_type=notification_type,
                variables=merged,
                destination=override or (config.notification_email if config else None),
                crypto=crypto,
                scope=scope,
                now=now,
            )
        else:
            result = await _dispatch_sms(
                session,
                tenant_id=tenant_id,
                call_id=call_id,
                notification_type=notification_type,
                variables=merged,
                destination=override or (config.escalation_number if config else None),
                crypto=crypto,
                scope=scope,
                timezone=(config.timezone if config else None),
                now=now,
            )
        if result is not None:
            results.append(result)
    return results


async def _dispatch_email(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID | None,
    notification_type: NotificationType,
    variables: dict[str, str],
    destination: str | None,
    crypto: EncryptionService,
    scope: str,
    now: datetime,
) -> DispatchResult | None:
    provider = get_email_provider()
    template = _EMAIL_TEMPLATES.get(notification_type)
    if provider is None or destination is None or template is None:
        return None
    try:
        assert_approved_template(template.value, channel="email")
    except NotificationPolicyError:
        logger.warning("notification_template_rejected", template=template.value)
        return None

    key = f"notify:{scope}:{notification_type.value}:email"
    delivery = await _claim(
        session,
        tenant_id=tenant_id,
        call_id=call_id,
        notification_type=notification_type,
        channel=NotificationChannel.EMAIL,
        template=template.value,
        recipient_masked=mask_email(destination),
        idempotency_key=key,
    )
    if delivery is None:
        return DispatchResult(
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.SENT,
            reason="already_sent",
        )

    if await _email_suppressed(session, tenant_id, destination, crypto):
        delivery.status = NotificationStatus.SUPPRESSED
        delivery.failure_category = "unsubscribed"
        await session.flush()
        return DispatchResult(
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.SUPPRESSED,
            reason="unsubscribed",
        )

    delivery.attempts += 1
    try:
        sent = await provider.send_template(
            to_email=destination,
            template=template.value,
            variables=variables,
            idempotency_key=key,
        )
    except DuplicateSendError:
        delivery.status = NotificationStatus.SENT
        await session.flush()
        return DispatchResult(
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.SENT,
            reason="already_sent",
        )
    except ProviderError as exc:
        delivery.status = NotificationStatus.FAILED
        delivery.failure_category = exc.category
        # Transient failures stay retryable; the job's own bounded retry
        # re-enters here and the idempotency key still guards the send.
        delivery.provider_response = {"transient": exc.transient}
        await session.flush()
        logger.warning("notification_send_failed", channel="email", error=exc.category)
        return DispatchResult(
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.FAILED,
            reason=exc.category,
        )

    delivery.status = NotificationStatus.SENT
    delivery.provider_message_id = sent.provider_message_id
    delivery.provider_response = {"accepted": sent.accepted}
    delivery.sent_at = now
    await session.flush()
    return DispatchResult(
        channel=NotificationChannel.EMAIL,
        status=NotificationStatus.SENT,
        provider_message_id=sent.provider_message_id,
    )


async def _dispatch_sms(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID | None,
    notification_type: NotificationType,
    variables: dict[str, str],
    destination: str | None,
    crypto: EncryptionService,
    scope: str,
    timezone: str | None,
    now: datetime,
) -> DispatchResult | None:
    provider = get_sms_provider()
    template = _SMS_TEMPLATES.get(notification_type)
    if provider is None or destination is None or template is None:
        return None

    key = f"notify:{scope}:{notification_type.value}:sms"
    delivery = await _claim(
        session,
        tenant_id=tenant_id,
        call_id=call_id,
        notification_type=notification_type,
        channel=NotificationChannel.SMS,
        template=template.value,
        recipient_masked=mask_phone(destination),
        idempotency_key=key,
    )
    if delivery is None:
        return DispatchResult(
            channel=NotificationChannel.SMS,
            status=NotificationStatus.SENT,
            reason="already_sent",
        )

    consent = await _sms_consent(session, tenant_id, destination, crypto)
    country = consent.country if consent else _country_from_e164(destination)
    policy = sms_policy_for_country(country)
    zone = _zone(timezone)
    decision = evaluate_sms(
        policy=policy,
        consent_granted=consent is not None and consent.status is ConsentStatus.GRANTED,
        is_emergency=notification_type in _EMERGENCY_TYPES,
        local_time=(now.astimezone(zone).time() if zone else None),
    )
    if not decision.allowed:
        delivery.status = NotificationStatus.SUPPRESSED
        delivery.failure_category = decision.reason
        await session.flush()
        logger.info(
            "sms_suppressed",
            to=mask_phone(destination),
            reason=decision.reason,
            country=country,
        )
        return DispatchResult(
            channel=NotificationChannel.SMS,
            status=NotificationStatus.SUPPRESSED,
            reason=decision.reason,
        )

    delivery.attempts += 1
    try:
        sent = await provider.send_template(
            to_e164=destination,
            template=template.value,
            variables={**variables, "opt_out_text": decision.opt_out_text},
            idempotency_key=key,
        )
    except DuplicateSendError:
        delivery.status = NotificationStatus.SENT
        await session.flush()
        return DispatchResult(
            channel=NotificationChannel.SMS,
            status=NotificationStatus.SENT,
            reason="already_sent",
        )
    except ProviderError as exc:
        delivery.status = NotificationStatus.FAILED
        delivery.failure_category = exc.category
        delivery.provider_response = {"transient": exc.transient}
        await session.flush()
        logger.warning(
            "notification_send_failed",
            channel="sms",
            to=mask_phone(destination),
            error=exc.category,
        )
        return DispatchResult(
            channel=NotificationChannel.SMS,
            status=NotificationStatus.FAILED,
            reason=exc.category,
        )

    delivery.status = NotificationStatus.SENT
    delivery.provider_message_id = sent.provider_message_id
    delivery.provider_response = {"accepted": sent.accepted}
    delivery.sent_at = now
    await session.flush()
    return DispatchResult(
        channel=NotificationChannel.SMS,
        status=NotificationStatus.SENT,
        provider_message_id=sent.provider_message_id,
    )


async def record_delivery_callback(
    session: AsyncSession, *, provider_message_id: str, status: str, now: datetime | None = None
) -> NotificationDelivery | None:
    """Apply a provider delivery callback to its delivery row."""
    now = now or datetime.now(UTC)
    delivery = (
        await session.execute(
            select(NotificationDelivery).where(
                NotificationDelivery.provider_message_id == provider_message_id
            )
        )
    ).scalar_one_or_none()
    if delivery is None:
        return None

    normalized = status.lower()
    if normalized in ("delivered", "read"):
        delivery.status = NotificationStatus.DELIVERED
        delivery.delivered_at = now
    elif normalized in ("failed", "undelivered", "bounced"):
        delivery.status = NotificationStatus.FAILED
        delivery.failure_category = normalized
    delivery.provider_response = {**(delivery.provider_response or {}), "callback": normalized}
    await session.flush()
    return delivery


async def resolve_notify_address(session: AsyncSession, *, call_id: uuid.UUID) -> str | None:
    """The tenant's configured notification email, if any."""
    from ai_database.models import Call

    row = (
        await session.execute(
            select(TenantConfig.notification_email)
            .join(Call, Call.tenant_id == TenantConfig.tenant_id)
            .where(Call.id == call_id)
        )
    ).scalar_one_or_none()
    return row or None
