"""Notification policy: what may be sent, on which channel, to whom.

Two rules shape this module.

**Templates are a closed set.** Outbound content is never free text — a
notification names an approved template and supplies variables. Anything
not in the catalog is refused before it reaches a provider.

**Nothing sensitive leaves the platform.** Transcripts, recordings, and
message bodies stay in the dashboard; notifications carry a summary and a
link. ``assert_safe_variables`` enforces that at the boundary rather than
trusting each call site.

SMS additionally requires consent, and consent rules are *not* uniform
across countries — see :func:`sms_policy_for_country`.
"""

from dataclasses import dataclass, field
from datetime import time
from enum import StrEnum


class NotificationTemplate(StrEnum):
    """Approved templates. Adding one is a deliberate, reviewed change."""

    # email
    BOOKING_CONFIRMATION = "booking_confirmation"
    NEW_MESSAGE = "new_message"
    URGENT_ESCALATION = "urgent_escalation"
    FAILED_CALL_ALERT = "failed_call_alert"
    DAILY_SUMMARY = "daily_summary"
    WEEKLY_REPORT = "weekly_report"
    CALENDAR_DISCONNECTED = "calendar_disconnected"
    # sms
    SMS_NEW_BOOKING = "sms_new_booking"
    SMS_URGENT_MESSAGE = "sms_urgent_message"
    SMS_EMERGENCY = "sms_emergency"


EMAIL_TEMPLATES = frozenset(
    {
        NotificationTemplate.BOOKING_CONFIRMATION,
        NotificationTemplate.NEW_MESSAGE,
        NotificationTemplate.URGENT_ESCALATION,
        NotificationTemplate.FAILED_CALL_ALERT,
        NotificationTemplate.DAILY_SUMMARY,
        NotificationTemplate.WEEKLY_REPORT,
        NotificationTemplate.CALENDAR_DISCONNECTED,
    }
)
SMS_TEMPLATES = frozenset(
    {
        NotificationTemplate.SMS_NEW_BOOKING,
        NotificationTemplate.SMS_URGENT_MESSAGE,
        NotificationTemplate.SMS_EMERGENCY,
    }
)

#: Variable names that must never appear in an outbound notification.
#: Summaries are allowed; the underlying conversation is not.
FORBIDDEN_VARIABLES = frozenset(
    {
        "transcript",
        "transcript_text",
        "turns",
        "recording_url",
        "recording",
        "message_body",
        "body",
        "customer_phone",
        "phone",
        "address",
        "notes",
    }
)

#: Longest a value may be before it stops being a summary and starts
#: being a transcript by another name.
MAX_VARIABLE_CHARS = 500


class NotificationPolicyError(Exception):
    """A notification violated the template or content policy."""


def assert_approved_template(template: str, *, channel: str) -> NotificationTemplate:
    """Return the template if it is approved for the channel."""
    try:
        resolved = NotificationTemplate(template)
    except ValueError as exc:
        raise NotificationPolicyError(f"template '{template}' is not approved") from exc
    allowed = SMS_TEMPLATES if channel == "sms" else EMAIL_TEMPLATES
    if resolved not in allowed:
        raise NotificationPolicyError(f"template '{template}' is not valid for {channel}")
    return resolved


def assert_safe_variables(variables: dict[str, str]) -> None:
    """Refuse sensitive content before it reaches a provider."""
    for key, value in variables.items():
        if key.lower() in FORBIDDEN_VARIABLES:
            raise NotificationPolicyError(f"variable '{key}' may not be sent in a notification")
        if len(value) > MAX_VARIABLE_CHARS:
            raise NotificationPolicyError(
                f"variable '{key}' exceeds {MAX_VARIABLE_CHARS} characters; "
                "notifications carry summaries, not full content"
            )


@dataclass(frozen=True)
class SmsPolicy:
    """Per-country SMS rules.

    ``requires_explicit_consent`` gates sending at all;
    ``quiet_hours`` blocks non-emergency sends in local time;
    ``requires_opt_out_language`` appends the STOP instruction.
    """

    country: str
    requires_explicit_consent: bool = True
    requires_opt_out_language: bool = True
    quiet_hours: tuple[time, time] | None = None
    #: emergencies override quiet hours, never consent
    emergency_overrides_quiet_hours: bool = True
    notes: str = ""


#: Rules differ by jurisdiction; the default is the strict one, so an
#: unlisted country is never treated as permissive by accident.
_SMS_POLICIES: dict[str, SmsPolicy] = {
    "US": SmsPolicy(
        country="US",
        requires_explicit_consent=True,
        requires_opt_out_language=True,
        quiet_hours=(time(21, 0), time(8, 0)),
        notes="TCPA: prior express consent; 8pm-9am local quiet hours.",
    ),
    "CA": SmsPolicy(
        country="CA",
        requires_explicit_consent=True,
        requires_opt_out_language=True,
        quiet_hours=(time(21, 0), time(8, 0)),
        notes="CASL: express consent and identification required.",
    ),
    "GB": SmsPolicy(
        country="GB",
        requires_explicit_consent=True,
        requires_opt_out_language=True,
        quiet_hours=None,
        notes="PECR: consent required; no statutory quiet hours.",
    ),
    "AU": SmsPolicy(
        country="AU",
        requires_explicit_consent=True,
        requires_opt_out_language=True,
        quiet_hours=None,
        notes="Spam Act: consent, sender identification, unsubscribe.",
    ),
}

#: Applied to any country without an explicit entry — strictest option.
DEFAULT_SMS_POLICY = SmsPolicy(
    country="ZZ",
    requires_explicit_consent=True,
    requires_opt_out_language=True,
    quiet_hours=(time(21, 0), time(8, 0)),
    notes="Unknown jurisdiction: strictest defaults until reviewed.",
)


def sms_policy_for_country(country: str | None) -> SmsPolicy:
    """The rules for a destination. Unknown countries get the strict
    default — never the US rules by assumption."""
    if not country:
        return DEFAULT_SMS_POLICY
    return _SMS_POLICIES.get(country.upper(), DEFAULT_SMS_POLICY)


def in_quiet_hours(policy: SmsPolicy, local: time) -> bool:
    """True when ``local`` falls inside the policy's quiet window.

    The window wraps midnight (e.g. 21:00 → 08:00).
    """
    if policy.quiet_hours is None:
        return False
    start, end = policy.quiet_hours
    if start <= end:
        return start <= local < end
    return local >= start or local < end


@dataclass(frozen=True)
class ChannelDecision:
    """Whether a channel may be used, and why not when it may not."""

    allowed: bool
    reason: str = ""
    #: appended to the message body when the jurisdiction requires it
    opt_out_text: str = ""


def evaluate_sms(
    *,
    policy: SmsPolicy,
    consent_granted: bool,
    is_emergency: bool,
    local_time: time | None = None,
) -> ChannelDecision:
    """Decide whether one SMS may be sent right now."""
    if policy.requires_explicit_consent and not consent_granted:
        return ChannelDecision(allowed=False, reason="no_consent")
    if (
        local_time is not None
        and in_quiet_hours(policy, local_time)
        and not (is_emergency and policy.emergency_overrides_quiet_hours)
    ):
        return ChannelDecision(allowed=False, reason="quiet_hours")
    return ChannelDecision(
        allowed=True,
        opt_out_text="Reply STOP to opt out." if policy.requires_opt_out_language else "",
    )


@dataclass(frozen=True)
class TenantBranding:
    """Branding merged into every email. Falls back to the platform name
    so a half-configured tenant still sends a coherent message."""

    business_name: str
    reply_to: str | None = None
    website: str | None = None
    support_phone: str | None = None

    def as_variables(self) -> dict[str, str]:
        out = {"business_name": self.business_name}
        if self.website:
            out["business_website"] = self.website
        if self.support_phone:
            out["business_phone"] = self.support_phone
        return out


@dataclass
class NotificationRequest:
    """One notification, fully resolved and ready to dispatch."""

    template: NotificationTemplate
    variables: dict[str, str] = field(default_factory=dict)
    is_emergency: bool = False


__all__ = [
    "DEFAULT_SMS_POLICY",
    "EMAIL_TEMPLATES",
    "FORBIDDEN_VARIABLES",
    "MAX_VARIABLE_CHARS",
    "SMS_TEMPLATES",
    "ChannelDecision",
    "NotificationPolicyError",
    "NotificationRequest",
    "NotificationTemplate",
    "SmsPolicy",
    "TenantBranding",
    "assert_approved_template",
    "assert_safe_variables",
    "evaluate_sms",
    "in_quiet_hours",
    "sms_policy_for_country",
]
