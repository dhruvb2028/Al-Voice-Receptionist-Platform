"""Resend email provider.

Templates are rendered here rather than in Resend so the approved-content
rule is enforced in our own code: the catalog of subjects and bodies is
in this module, and an unknown template never reaches the API.

Idempotency is delegated to Resend's ``Idempotency-Key`` header, with a
local guard so a retry inside one process cannot double-send either.
"""

import html
from typing import Any

import httpx
import structlog

from ai_providers.errors import (
    DuplicateSendError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ai_providers.messaging import SendResult

logger = structlog.get_logger()

RESEND_API_BASE = "https://api.resend.com"

#: subject + plain-text body per approved template. Bodies are built from
#: variables only — never from caller-supplied markup.
_TEMPLATES: dict[str, tuple[str, str]] = {
    "booking_confirmation": (
        "New booking for {business_name}",
        "Your receptionist booked an appointment.\n\n"
        "When: {time}\n"
        "Summary: {summary}\n\n"
        "Open your dashboard for the full details.",
    ),
    "new_message": (
        "New message for {business_name}",
        "Your receptionist took a message.\n\nSummary: {summary}\n\n"
        "Open your dashboard to read it.",
    ),
    "urgent_escalation": (
        "Urgent: caller needs attention at {business_name}",
        "Your receptionist flagged an urgent call.\n\nSummary: {summary}\n\n"
        "Open your dashboard now.",
    ),
    "failed_call_alert": (
        "A call could not be completed at {business_name}",
        "A call ended without being handled.\n\nSummary: {summary}\n\n"
        "Open your dashboard to review it.",
    ),
    "daily_summary": (
        "Yesterday at {business_name}",
        "Calls answered: {calls_answered}\n"
        "Appointments booked: {appointments_booked}\n"
        "Messages taken: {messages_captured}\n\n"
        "Open your dashboard for the detail.",
    ),
    "weekly_report": (
        "This week at {business_name}",
        "Calls answered: {calls_answered}\n"
        "Appointments booked: {appointments_booked}\n"
        "Messages taken: {messages_captured}\n"
        "Handled without a human: {containment_rate}\n\n"
        "Open your dashboard for the detail.",
    ),
    "calendar_disconnected": (
        "Action needed: calendar disconnected for {business_name}",
        "Your receptionist can no longer write to your calendar, so new "
        "bookings are not being added.\n\n"
        "Reconnect it from the dashboard integrations page.",
    ),
    "owner_invitation": (
        "You've been invited to {business_name}",
        "An account has been created for you.\n\nOpen the dashboard to finish signing in.",
    ),
}


def render_template(template: str, variables: dict[str, str]) -> tuple[str, str]:
    """(subject, body) for an approved template.

    Missing variables render as an empty string rather than raising: a
    notification with a blank field still reaches the business, while a
    crash means they hear nothing at all.
    """
    if template not in _TEMPLATES:
        raise ProviderResponseError(f"template '{template}' is not approved")
    subject_tpl, body_tpl = _TEMPLATES[template]
    safe = _Defaulting(variables)
    return subject_tpl.format_map(safe), body_tpl.format_map(safe)


class _Defaulting(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _as_html(body: str, *, footer: str = "") -> str:
    paragraphs = "".join(f"<p>{html.escape(line)}</p>" for line in body.split("\n") if line.strip())
    if footer:
        paragraphs += f'<hr><p style="font-size:12px;color:#64748b">{html.escape(footer)}</p>'
    return f'<div style="font-family:system-ui,sans-serif;line-height:1.5">{paragraphs}</div>'


class ResendEmailProvider:
    """EmailProvider implementation backed by Resend."""

    def __init__(
        self,
        *,
        api_key: str,
        from_address: str,
        http: httpx.AsyncClient | None = None,
        base_url: str = RESEND_API_BASE,
    ) -> None:
        self._from = from_address
        self._http = http or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        self._seen: set[str] = set()

    async def send_template(
        self,
        *,
        to_email: str,
        template: str,
        variables: dict[str, str],
        idempotency_key: str,
    ) -> SendResult:
        if idempotency_key in self._seen:
            raise DuplicateSendError("idempotency key already used", provider="resend")
        subject, body = render_template(template, variables)

        payload: dict[str, Any] = {
            "from": variables.get("from_address") or self._from,
            "to": [to_email],
            "subject": subject,
            "text": body,
            "html": _as_html(body, footer=variables.get("footer", "")),
        }
        if reply_to := variables.get("reply_to"):
            payload["reply_to"] = reply_to

        try:
            response = await self._http.post(
                "/emails",
                json=payload,
                headers={"Idempotency-Key": idempotency_key},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("resend timed out", provider="resend") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("resend unreachable", provider="resend") from exc

        if response.status_code in (401, 403):
            raise ProviderAuthError("resend rejected the API key", provider="resend")
        if response.status_code == 429:
            raise ProviderRateLimitError("resend rate limited", provider="resend")
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"resend returned {response.status_code}", provider="resend"
            )
        if response.status_code >= 400:
            raise ProviderResponseError(
                f"resend rejected the request ({response.status_code})", provider="resend"
            )

        try:
            message_id = str(response.json()["id"])
        except (ValueError, KeyError) as exc:
            raise ProviderResponseError("resend response had no id", provider="resend") from exc

        self._seen.add(idempotency_key)
        return SendResult(provider_message_id=message_id, accepted=True)
