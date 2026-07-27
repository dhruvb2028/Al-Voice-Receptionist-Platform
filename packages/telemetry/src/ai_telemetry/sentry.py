"""Sentry configuration shared by every service.

Sentry sees stack traces and request shapes — never conversation
content. :func:`scrub_event` runs as ``before_send`` and strips anything
personal before the event leaves the process, so a new call site cannot
leak by forgetting to be careful. The scrubber is a plain function so it
is testable without the SDK installed.
"""

from collections.abc import Iterable
from typing import Any

import structlog

from ai_telemetry.logging import REDACTED, SENSITIVE_KEY_FRAGMENTS, SENSITIVE_KEYS

logger = structlog.get_logger()

#: Request headers that must never reach Sentry.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-twilio-signature",
        "upstash-signature",
        "proxy-authorization",
    }
)

#: Local-variable and context names holding conversation content.
CONVERSATION_KEYS = frozenset(
    {
        "transcript",
        "caller_text",
        "reply_text",
        "text",
        "body",
        "message",
        "messages",
        "audio",
        "payload",
    }
)

_MAX_DEPTH = 4


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in SENSITIVE_KEYS or lowered in CONVERSATION_KEYS:
        return True
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            k: REDACTED if _is_sensitive(str(k)) else _scrub(v, depth + 1) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item, depth + 1) for item in value]
    return value


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """``before_send``: remove personal and conversation data.

    Kept deliberately blunt. An over-scrubbed event is still actionable
    from its stack trace; an under-scrubbed one is a privacy incident.
    """
    event.pop("user", None)

    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                name: (REDACTED if name.lower() in SENSITIVE_HEADERS else value)
                for name, value in headers.items()
            }
        # Bodies may carry a transcript or a caller's details verbatim.
        request.pop("data", None)
        request.pop("cookies", None)
        # Query strings should not carry personal data, but if one does
        # we would rather lose the detail than publish it.
        if request.get("query_string"):
            request["query_string"] = REDACTED

    for section in ("extra", "contexts", "tags"):
        value = event.get(section)
        if isinstance(value, dict):
            event[section] = _scrub(value)

    for entry in _stack_frames(event):
        variables = entry.get("vars")
        if isinstance(variables, dict):
            entry["vars"] = _scrub(variables)

    if isinstance(event.get("breadcrumbs"), dict):
        crumbs = event["breadcrumbs"].get("values")
        if isinstance(crumbs, list):
            for crumb in crumbs:
                if isinstance(crumb, dict) and isinstance(crumb.get("data"), dict):
                    crumb["data"] = _scrub(crumb["data"])

    return event


def _stack_frames(event: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for exception in (event.get("exception") or {}).get("values", []) or []:
        for frame in (exception.get("stacktrace") or {}).get("frames", []) or []:
            if isinstance(frame, dict):
                yield frame


def configure_sentry(
    *,
    dsn: str | None,
    environment: str,
    service_name: str,
    release: str | None = None,
    traces_sample_rate: float = 0.1,
) -> bool:
    """Initialise Sentry for a service. Returns False when unconfigured.

    A missing DSN is normal in local development and CI — monitoring is
    optional infrastructure, so its absence must never stop a service
    from starting.
    """
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - deployed-only dependency
        logger.warning("sentry_sdk_missing")
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        before_send=scrub_event,
        # Personal data must never be attached automatically.
        send_default_pii=False,
        max_breadcrumbs=25,
    )
    sentry_sdk.set_tag("service", service_name)
    logger.info("sentry_configured", environment=environment, service=service_name)
    return True


__all__ = [
    "CONVERSATION_KEYS",
    "SENSITIVE_HEADERS",
    "configure_sentry",
    "scrub_event",
]
