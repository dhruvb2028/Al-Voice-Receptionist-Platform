"""Resend and Twilio SMS adapters: template rendering, error mapping,
idempotency, and phone masking."""

import httpx
import pytest
from ai_providers.errors import (
    DuplicateSendError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from ai_providers.resend import ResendEmailProvider, render_template
from ai_providers.twilio_sms import TwilioSMSProvider, mask_phone, render_sms


def _resend(handler: httpx.MockTransport) -> ResendEmailProvider:
    return ResendEmailProvider(
        api_key="re_test",
        from_address="alerts@receptionist.example",
        http=httpx.AsyncClient(transport=handler, base_url="https://api.resend.test"),
    )


def _sms(handler: httpx.MockTransport, **kwargs: object) -> TwilioSMSProvider:
    return TwilioSMSProvider(
        account_sid="AC123",
        auth_token="token",
        from_number="+15550001111",
        http=httpx.AsyncClient(transport=handler, base_url="https://api.twilio.test"),
        **kwargs,  # type: ignore[arg-type]
    )


# --- Resend ------------------------------------------------------------------


def test_render_template_fills_variables() -> None:
    subject, body = render_template(
        "booking_confirmation",
        {"business_name": "Ace Plumbing", "time": "Tue 10am", "summary": "Drain cleaning"},
    )
    assert subject == "New booking for Ace Plumbing"
    assert "Tue 10am" in body
    assert "Drain cleaning" in body


def test_render_template_tolerates_missing_variables() -> None:
    """A blank field still reaches the business; a crash means silence."""
    subject, body = render_template("new_message", {"business_name": "Ace"})
    assert subject == "New message for Ace"
    assert "Summary:" in body


def test_render_unknown_template_refused() -> None:
    with pytest.raises(ProviderResponseError, match="not approved"):
        render_template("anything_goes", {})


async def test_email_send_returns_provider_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "msg_abc"})

    provider = _resend(httpx.MockTransport(handler))
    result = await provider.send_template(
        to_email="owner@example.com",
        template="booking_confirmation",
        variables={"business_name": "Ace", "time": "Tue", "summary": "s"},
        idempotency_key="notify:1:new_booking:email",
    )
    assert result.provider_message_id == "msg_abc"
    assert seen[0].headers["Idempotency-Key"] == "notify:1:new_booking:email"


async def test_email_reuse_of_key_raises_duplicate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "msg_abc"})

    provider = _resend(httpx.MockTransport(handler))
    kwargs = {
        "to_email": "owner@example.com",
        "template": "new_message",
        "variables": {"business_name": "Ace"},
        "idempotency_key": "same-key",
    }
    await provider.send_template(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(DuplicateSendError):
        await provider.send_template(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderAuthError),
        (429, ProviderRateLimitError),
        (503, ProviderUnavailableError),
        (422, ProviderResponseError),
    ],
)
async def test_email_error_mapping(status: int, expected: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    provider = _resend(httpx.MockTransport(handler))
    with pytest.raises(expected):
        await provider.send_template(
            to_email="owner@example.com",
            template="new_message",
            variables={},
            idempotency_key="k",
        )


async def test_email_timeout_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    provider = _resend(httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc_info:
        await provider.send_template(
            to_email="o@example.com", template="new_message", variables={}, idempotency_key="k"
        )
    assert getattr(exc_info.value, "transient", False) is True


# --- Twilio SMS --------------------------------------------------------------


def test_mask_phone_keeps_only_last_four() -> None:
    assert mask_phone("+15551234821") == "···4821"
    assert "5551234" not in mask_phone("+15551234821")


def test_render_sms_appends_opt_out() -> None:
    body = render_sms(
        "sms_urgent_message", {"business_name": "Ace"}, opt_out_text="Reply STOP to opt out."
    )
    assert body.startswith("Ace: urgent message")
    assert body.endswith("Reply STOP to opt out.")


def test_render_sms_unknown_template_refused() -> None:
    with pytest.raises(ProviderResponseError, match="not approved"):
        render_sms("sms_freeform", {})


async def test_sms_send_posts_form_and_callback() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"sid": "SM123", "status": "queued"})

    provider = _sms(
        httpx.MockTransport(handler), status_callback_url="https://api.example/sms/status"
    )
    result = await provider.send_template(
        to_e164="+15551234821",
        template="sms_emergency",
        variables={"business_name": "Ace"},
        idempotency_key="notify:1:emergency_escalation:sms",
    )
    assert result.provider_message_id == "SM123"
    body = seen[0].content.decode()
    assert "To=%2B15551234821" in body
    assert "StatusCallback" in body


async def test_sms_duplicate_key_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"sid": "SM1"})

    provider = _sms(httpx.MockTransport(handler))
    kwargs = {
        "to_e164": "+15551234821",
        "template": "sms_new_booking",
        "variables": {},
        "idempotency_key": "same",
    }
    await provider.send_template(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(DuplicateSendError):
        await provider.send_template(**kwargs)  # type: ignore[arg-type]


async def test_sms_delivery_status_lookup() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sid": "SM1", "status": "delivered"})

    provider = _sms(httpx.MockTransport(handler))
    assert await provider.delivery_status(provider_message_id="SM1") == "delivered"


async def test_sms_error_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    provider = _sms(httpx.MockTransport(handler))
    with pytest.raises(ProviderAuthError):
        await provider.send_template(
            to_e164="+15551234821",
            template="sms_new_booking",
            variables={},
            idempotency_key="k",
        )
