"""structlog configuration shared by every service.

Output is JSON in deployed environments (one line per event, Cloud
Logging compatible) and pretty console rendering locally. Sensitive
fields are redacted at the processor level — one choke point instead of
per-call-site discipline.
"""

import logging
import re
import sys
from typing import Any

import structlog

# Field names whose values are always redacted, wherever they appear.
SENSITIVE_KEYS = frozenset(
    {
        "caller_phone",
        "caller_name",
        "customer_name",
        "customer_phone",
        "phone_number",
        "phone",
        "to_number",
        "from_number",
        "address",
        "transcript",
        "body",
        "message_body",
        "note",
        "internal_note",
        "email",
        "to_email",
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "signing_key",
        "secret",
        "password",
        "signature",
        "cookie",
        "set-cookie",
    }
)

# Substrings that make any key sensitive, so a new field named
# `groq_api_key` or `caller_phone_e164` is covered without a code change.
SENSITIVE_KEY_FRAGMENTS = ("password", "secret", "token", "api_key", "auth", "signature")

# E.164-ish phone numbers embedded inside free-text values.
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
# Email addresses embedded in free text.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

REDACTED = "[redacted]"

#: Depth limit for nested structures — deep enough for real payloads,
#: bounded so a cyclic or pathological object cannot stall logging.
_MAX_DEPTH = 4


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in SENSITIVE_KEYS:
        return True
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def _redact_value(key: str, value: Any, depth: int = 0) -> Any:
    if _is_sensitive(key):
        return REDACTED
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        redacted = [_redact_value(key, item, depth + 1) for item in value]
        return type(value)(redacted) if isinstance(value, tuple) else redacted
    if isinstance(value, str):
        scrubbed = _PHONE_RE.sub(REDACTED, value)
        return _EMAIL_RE.sub(REDACTED, scrubbed)
    return value


def redact_sensitive(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    for key, value in list(event_dict.items()):
        event_dict[key] = _redact_value(key, value)
    return event_dict


def configure_logging(
    *, service_name: str, log_level: str = "INFO", json_output: bool = True
) -> None:
    """Configure structlog and stdlib logging for a service process."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")

    renderer: structlog.types.Processor
    renderer = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_sensitive,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service_name)
