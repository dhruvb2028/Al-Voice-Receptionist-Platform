"""Notification policy: approved templates, content safety, and the
country-specific SMS rules."""

from datetime import time

import pytest
from ai_domain.notifications import (
    DEFAULT_SMS_POLICY,
    NotificationPolicyError,
    NotificationTemplate,
    TenantBranding,
    assert_approved_template,
    assert_safe_variables,
    evaluate_sms,
    in_quiet_hours,
    sms_policy_for_country,
)


def test_approved_email_template() -> None:
    assert (
        assert_approved_template("booking_confirmation", channel="email")
        is NotificationTemplate.BOOKING_CONFIRMATION
    )


def test_unknown_template_rejected() -> None:
    with pytest.raises(NotificationPolicyError):
        assert_approved_template("free_text_blast", channel="email")


def test_sms_template_rejected_on_email_channel() -> None:
    with pytest.raises(NotificationPolicyError, match="not valid for email"):
        assert_approved_template("sms_emergency", channel="email")


def test_email_template_rejected_on_sms_channel() -> None:
    with pytest.raises(NotificationPolicyError, match="not valid for sms"):
        assert_approved_template("booking_confirmation", channel="sms")


def test_summary_variables_are_allowed() -> None:
    assert_safe_variables({"summary": "Caller booked a drain cleaning.", "time": "Tue 10am"})


@pytest.mark.parametrize(
    "key",
    ["transcript", "recording_url", "message_body", "customer_phone", "address"],
)
def test_sensitive_variables_are_refused(key: str) -> None:
    with pytest.raises(NotificationPolicyError, match="may not be sent"):
        assert_safe_variables({key: "anything"})


def test_oversized_variable_is_refused() -> None:
    """A 'summary' long enough to be the transcript is not a summary."""
    with pytest.raises(NotificationPolicyError, match="exceeds"):
        assert_safe_variables({"summary": "x" * 501})


def test_unknown_country_gets_the_strict_default() -> None:
    policy = sms_policy_for_country("XK")
    assert policy is DEFAULT_SMS_POLICY
    assert policy.requires_explicit_consent
    assert policy.quiet_hours is not None


def test_country_rules_are_not_uniform() -> None:
    """The UK has no statutory quiet hours; the US does. Applying US
    rules everywhere would be wrong in both directions."""
    assert sms_policy_for_country("US").quiet_hours == (time(21, 0), time(8, 0))
    assert sms_policy_for_country("GB").quiet_hours is None
    assert sms_policy_for_country("gb").country == "GB"  # case-insensitive


def test_quiet_hours_window_wraps_midnight() -> None:
    policy = sms_policy_for_country("US")
    assert in_quiet_hours(policy, time(22, 30)) is True
    assert in_quiet_hours(policy, time(3, 0)) is True
    assert in_quiet_hours(policy, time(7, 59)) is True
    assert in_quiet_hours(policy, time(8, 0)) is False
    assert in_quiet_hours(policy, time(14, 0)) is False


def test_sms_blocked_without_consent() -> None:
    decision = evaluate_sms(
        policy=sms_policy_for_country("US"), consent_granted=False, is_emergency=False
    )
    assert decision.allowed is False
    assert decision.reason == "no_consent"


def test_emergency_never_overrides_missing_consent() -> None:
    decision = evaluate_sms(
        policy=sms_policy_for_country("US"), consent_granted=False, is_emergency=True
    )
    assert decision.allowed is False
    assert decision.reason == "no_consent"


def test_quiet_hours_block_routine_but_not_emergency() -> None:
    policy = sms_policy_for_country("US")
    routine = evaluate_sms(
        policy=policy, consent_granted=True, is_emergency=False, local_time=time(23, 0)
    )
    assert routine.allowed is False
    assert routine.reason == "quiet_hours"

    emergency = evaluate_sms(
        policy=policy, consent_granted=True, is_emergency=True, local_time=time(23, 0)
    )
    assert emergency.allowed is True


def test_allowed_send_carries_opt_out_language() -> None:
    decision = evaluate_sms(
        policy=sms_policy_for_country("US"),
        consent_granted=True,
        is_emergency=False,
        local_time=time(10, 0),
    )
    assert decision.allowed is True
    assert "STOP" in decision.opt_out_text


def test_branding_variables() -> None:
    branding = TenantBranding(business_name="Ace Plumbing", website="https://ace.example")
    variables = branding.as_variables()
    assert variables["business_name"] == "Ace Plumbing"
    assert variables["business_website"] == "https://ace.example"
    assert "support_phone" not in variables
