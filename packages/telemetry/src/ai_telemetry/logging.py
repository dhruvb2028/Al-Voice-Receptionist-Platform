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
        "phone_number",
        "address",
        "transcript",
        "authorization",
        "token",
        "api_key",
        "secret",
        "password",
    }
)

# E.164-ish phone numbers embedded inside free-text values.
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")

REDACTED = "[redacted]"


def _redact_value(key: str, value: Any) -> Any:
    if key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str) and _PHONE_RE.search(value):
        return _PHONE_RE.sub(REDACTED, value)
    return value


def redact_sensitive(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    for key, value in list(event_dict.items()):
        if isinstance(value, dict):
            event_dict[key] = {k: _redact_value(k, v) for k, v in value.items()}
        else:
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
