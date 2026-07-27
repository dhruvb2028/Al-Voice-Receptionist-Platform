"""SMS and email provider interfaces (Twilio SMS / Resend in production).

Both send only pre-approved templates with variables — free-text
outbound content is not a capability the platform offers.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from ai_providers.errors import DuplicateSendError


class SendResult(BaseModel):
    provider_message_id: str
    accepted: bool


@runtime_checkable
class SMSProvider(Protocol):
    async def send_template(
        self,
        *,
        to_e164: str,
        template: str,
        variables: dict[str, str],
        idempotency_key: str,
    ) -> SendResult:
        """Send an approved template. A reused idempotency key raises
        DuplicateSendError — the original send stands."""
        ...

    async def delivery_status(self, *, provider_message_id: str) -> str: ...


@runtime_checkable
class EmailProvider(Protocol):
    async def send_template(
        self,
        *,
        to_email: str,
        template: str,
        variables: dict[str, str],
        idempotency_key: str,
    ) -> SendResult: ...


class _TemplateSender:
    def __init__(self, *, approved_templates: set[str]) -> None:
        self.approved_templates = approved_templates
        self.sent: list[dict[str, object]] = []
        self._by_key: dict[str, SendResult] = {}
        self.statuses: dict[str, str] = {}

    async def send_template(
        self,
        *,
        to: str,
        template: str,
        variables: dict[str, str],
        idempotency_key: str,
    ) -> SendResult:
        if template not in self.approved_templates:
            from ai_providers.errors import ProviderResponseError

            raise ProviderResponseError(f"template '{template}' is not approved")
        if idempotency_key in self._by_key:
            raise DuplicateSendError("idempotency key already used")
        result = SendResult(provider_message_id=f"msg_{len(self.sent) + 1}", accepted=True)
        self.sent.append(
            {"to": to, "template": template, "variables": variables, "key": idempotency_key}
        )
        self._by_key[idempotency_key] = result
        self.statuses[result.provider_message_id] = "delivered"
        return result


class MockSMSProvider(_TemplateSender):
    def __init__(self) -> None:
        # Mirrors ai_domain.notifications.SMS_TEMPLATES: the mock must
        # reject exactly what production rejects.
        super().__init__(
            approved_templates={
                "sms_new_booking",
                "sms_urgent_message",
                "sms_emergency",
                # legacy tool-layer templates
                "booking_confirmation",
                "message_ack",
                "escalation_alert",
            }
        )

    async def send_template(  # type: ignore[override]
        self,
        *,
        to_e164: str,
        template: str,
        variables: dict[str, str],
        idempotency_key: str,
    ) -> SendResult:
        return await super().send_template(
            to=to_e164, template=template, variables=variables, idempotency_key=idempotency_key
        )

    async def delivery_status(self, *, provider_message_id: str) -> str:
        return self.statuses.get(provider_message_id, "unknown")


class MockEmailProvider(_TemplateSender):
    def __init__(self) -> None:
        # Mirrors ai_domain.notifications.EMAIL_TEMPLATES.
        super().__init__(
            approved_templates={
                "booking_confirmation",
                "new_message",
                "urgent_escalation",
                "failed_call_alert",
                "daily_summary",
                "weekly_report",
                "calendar_disconnected",
                "owner_invitation",
            }
        )

    async def send_template(  # type: ignore[override]
        self,
        *,
        to_email: str,
        template: str,
        variables: dict[str, str],
        idempotency_key: str,
    ) -> SendResult:
        return await super().send_template(
            to=to_email, template=template, variables=variables, idempotency_key=idempotency_key
        )
