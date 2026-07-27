"""Twilio SMS provider.

Only approved template bodies are sent — the caller names a template and
supplies variables, never a message string. Destination numbers are
masked in every log line this module emits.
"""

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
from ai_providers.twilio import TWILIO_API_BASE

logger = structlog.get_logger()

#: SMS is length-sensitive; these stay inside a single segment where
#: possible and never carry message content, only a pointer to it.
_TEMPLATES: dict[str, str] = {
    "sms_new_booking": "{business_name}: new booking {time}. Details in your dashboard.",
    "sms_urgent_message": "{business_name}: urgent message from a caller. Check your dashboard.",
    "sms_emergency": "{business_name}: EMERGENCY call needs attention now. Check your dashboard.",
}


def mask_phone(e164: str) -> str:
    """Never log a full destination number."""
    digits = "".join(ch for ch in e164 if ch.isdigit())
    return f"···{digits[-4:]}" if len(digits) >= 4 else "···"


def render_sms(template: str, variables: dict[str, str], *, opt_out_text: str = "") -> str:
    if template not in _TEMPLATES:
        raise ProviderResponseError(f"template '{template}' is not approved", provider="twilio")
    body = _TEMPLATES[template].format_map(_Defaulting(variables))
    return f"{body} {opt_out_text}".strip() if opt_out_text else body


class _Defaulting(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


class TwilioSMSProvider:
    """SMSProvider implementation backed by Twilio's Messages API."""

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        status_callback_url: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._from = from_number
        self._status_callback = status_callback_url
        self._http = http or httpx.AsyncClient(
            base_url=f"{TWILIO_API_BASE}/Accounts/{account_sid}",
            auth=(account_sid, auth_token),
            timeout=10.0,
        )
        self._seen: set[str] = set()

    async def send_template(
        self,
        *,
        to_e164: str,
        template: str,
        variables: dict[str, str],
        idempotency_key: str,
    ) -> SendResult:
        if idempotency_key in self._seen:
            raise DuplicateSendError("idempotency key already used", provider="twilio")

        body = render_sms(template, variables, opt_out_text=variables.get("opt_out_text", ""))
        form: dict[str, str] = {"To": to_e164, "From": self._from, "Body": body}
        if self._status_callback:
            form["StatusCallback"] = self._status_callback

        try:
            response = await self._http.post("/Messages.json", data=form)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("twilio sms timed out", provider="twilio") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("twilio unreachable", provider="twilio") from exc

        if response.status_code in (401, 403):
            raise ProviderAuthError("twilio rejected the credentials", provider="twilio")
        if response.status_code == 429:
            raise ProviderRateLimitError("twilio rate limited", provider="twilio")
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"twilio returned {response.status_code}", provider="twilio"
            )
        if response.status_code >= 400:
            raise ProviderResponseError(
                f"twilio rejected the message ({response.status_code})", provider="twilio"
            )

        try:
            payload: dict[str, Any] = response.json()
            sid = str(payload["sid"])
        except (ValueError, KeyError) as exc:
            raise ProviderResponseError("twilio response had no sid", provider="twilio") from exc

        self._seen.add(idempotency_key)
        logger.info("sms_sent", to=mask_phone(to_e164), template=template)
        return SendResult(provider_message_id=sid, accepted=True)

    async def delivery_status(self, *, provider_message_id: str) -> str:
        try:
            response = await self._http.get(f"/Messages/{provider_message_id}.json")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("twilio unreachable", provider="twilio") from exc
        try:
            return str(response.json()["status"])
        except (ValueError, KeyError) as exc:
            raise ProviderResponseError("twilio response had no status", provider="twilio") from exc
