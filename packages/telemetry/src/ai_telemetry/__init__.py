"""Structured logging, metrics, and error reporting for all services."""

from ai_telemetry.logging import configure_logging
from ai_telemetry.metrics import (
    METRICS,
    MetricsRegistry,
    get_registry,
    increment,
    observe,
    set_gauge,
)
from ai_telemetry.sentry import configure_sentry, scrub_event

__all__ = [
    "METRICS",
    "MetricsRegistry",
    "configure_logging",
    "configure_observability",
    "configure_sentry",
    "get_registry",
    "increment",
    "observe",
    "scrub_event",
    "set_gauge",
]


def configure_observability(
    *,
    service_name: str,
    log_level: str = "INFO",
    environment: str = "local",
    sentry_dsn: str | None = None,
    sentry_release: str | None = None,
) -> None:
    """One call every service makes at startup.

    Logging is always configured; Sentry only when a DSN is present, so
    local runs and CI need no monitoring credentials.
    """
    configure_logging(
        service_name=service_name,
        log_level=log_level,
        json_output=environment != "local",
    )
    configure_sentry(
        dsn=sentry_dsn,
        environment=environment,
        service_name=service_name,
        release=sentry_release,
    )
