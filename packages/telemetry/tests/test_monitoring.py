"""Sentry scrubbing and the metrics registry.

The scrubber is the only thing standing between a stack trace and a
caller's transcript leaving the platform, so it is tested against the
event shapes Sentry actually produces.
"""

import pytest
from ai_telemetry.logging import REDACTED
from ai_telemetry.metrics import (
    METRIC_NAMES,
    METRICS,
    RESERVOIR_SIZE,
    MetricsRegistry,
)
from ai_telemetry.sentry import configure_sentry, scrub_event

# --- Sentry scrubbing --------------------------------------------------------


def test_user_context_is_dropped() -> None:
    event = scrub_event({"user": {"id": "user_1", "email": "pat@example.com"}})
    assert "user" not in event


def test_sensitive_headers_are_redacted() -> None:
    event = scrub_event(
        {
            "request": {
                "headers": {
                    "Authorization": "Bearer secret-token",
                    "X-Twilio-Signature": "sig",
                    "User-Agent": "twilio",
                }
            }
        }
    )
    headers = event["request"]["headers"]
    assert headers["Authorization"] == REDACTED
    assert headers["X-Twilio-Signature"] == REDACTED
    assert headers["User-Agent"] == "twilio"  # harmless, kept for debugging


def test_request_body_and_cookies_are_dropped() -> None:
    event = scrub_event(
        {
            "request": {
                "data": {"transcript": "the whole conversation"},
                "cookies": {"session": "abc"},
                "query_string": "phone=%2B15551234821",
            }
        }
    )
    assert "data" not in event["request"]
    assert "cookies" not in event["request"]
    assert event["request"]["query_string"] == REDACTED


def test_stack_frame_locals_are_scrubbed() -> None:
    """A traceback's local variables are the most likely place a
    transcript leaks."""
    event = scrub_event(
        {
            "exception": {
                "values": [
                    {
                        "stacktrace": {
                            "frames": [
                                {
                                    "function": "process_turn",
                                    "vars": {
                                        "caller_text": "my card number is ...",
                                        "transcript": "everything said",
                                        "turn_index": 3,
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        }
    )
    variables = event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert variables["caller_text"] == REDACTED
    assert variables["transcript"] == REDACTED
    assert variables["turn_index"] == 3


def test_extra_and_contexts_are_scrubbed() -> None:
    event = scrub_event(
        {
            "extra": {"api_key": "gsk_live", "call_id": "abc"},
            "contexts": {"call": {"caller_phone": "+15551234821", "outcome": "booked"}},
        }
    )
    assert event["extra"]["api_key"] == REDACTED
    assert event["extra"]["call_id"] == "abc"
    assert event["contexts"]["call"]["caller_phone"] == REDACTED
    assert event["contexts"]["call"]["outcome"] == "booked"


def test_breadcrumb_data_is_scrubbed() -> None:
    event = scrub_event(
        {"breadcrumbs": {"values": [{"data": {"body": "message text", "status": 200}}]}}
    )
    crumb = event["breadcrumbs"]["values"][0]["data"]
    assert crumb["body"] == REDACTED
    assert crumb["status"] == 200


def test_scrubbing_an_ordinary_event_changes_nothing_useful() -> None:
    event = scrub_event({"level": "error", "logger": "voice", "extra": {"call_id": "abc"}})
    assert event["level"] == "error"
    assert event["extra"]["call_id"] == "abc"


def test_sentry_without_a_dsn_is_a_no_op() -> None:
    """Monitoring is optional infrastructure; a missing DSN must never
    stop a service from starting."""
    assert configure_sentry(dsn=None, environment="local", service_name="api") is False


# --- metrics -----------------------------------------------------------------


def test_metric_catalog_names_are_unique() -> None:
    assert len(METRIC_NAMES) == len(METRICS)


def test_counters_accumulate() -> None:
    registry = MetricsRegistry()
    registry.increment("calls.failed")
    registry.increment("calls.failed", 2)
    assert registry.counter("calls.failed") == 3


def test_counters_are_separated_by_labels() -> None:
    registry = MetricsRegistry()
    registry.increment("calls.failed", labels={"tenant": "a"})
    registry.increment("calls.failed", labels={"tenant": "b"})
    assert registry.counter("calls.failed", {"tenant": "a"}) == 1
    assert registry.counter("calls.failed", {"tenant": "b"}) == 1
    assert registry.counter("calls.failed") == 0


def test_label_order_does_not_create_separate_series() -> None:
    registry = MetricsRegistry()
    registry.increment("x", labels={"a": "1", "b": "2"})
    registry.increment("x", labels={"b": "2", "a": "1"})
    assert registry.counter("x", {"a": "1", "b": "2"}) == 2


def test_gauges_replace_rather_than_accumulate() -> None:
    registry = MetricsRegistry()
    registry.set_gauge("calls.active", 3)
    registry.set_gauge("calls.active", 1)
    assert registry.gauge("calls.active") == 1


def test_percentiles_interpolate() -> None:
    registry = MetricsRegistry()
    for value in [100, 200, 300, 400, 500]:
        registry.observe("turn.response_latency", value)
    assert registry.percentile("turn.response_latency", 0.5) == 300
    assert registry.percentile("turn.response_latency", 0.95) == pytest.approx(480)


def test_percentile_of_an_unmeasured_metric_is_none() -> None:
    assert MetricsRegistry().percentile("turn.response_latency", 0.5) is None


def test_reservoir_is_bounded_and_keeps_the_tails() -> None:
    """Tail latency is what alerts fire on, so eviction must not eat the
    extremes."""
    registry = MetricsRegistry()
    for value in range(RESERVOIR_SIZE * 2):
        registry.observe("turn.response_latency", float(value))
    p95 = registry.percentile("turn.response_latency", 0.95)
    assert p95 is not None
    # The largest observation survived eviction.
    assert registry.percentile("turn.response_latency", 1.0) == float(RESERVOIR_SIZE * 2 - 1)
    assert registry.percentile("turn.response_latency", 0.0) == 0.0


def test_snapshot_includes_counters_gauges_and_percentiles() -> None:
    registry = MetricsRegistry()
    registry.increment("calls.failed", 2)
    registry.set_gauge("calls.active", 4)
    for value in [10, 20, 30]:
        registry.observe("turn.response_latency", value)
    snapshot = registry.snapshot()
    assert snapshot["calls.failed"] == 2
    assert snapshot["calls.active"] == 4
    assert snapshot["turn.response_latency.count"] == 3
    assert snapshot["turn.response_latency.p50"] == 20


def test_snapshot_renders_labels() -> None:
    registry = MetricsRegistry()
    registry.increment("calls.failed", labels={"tenant": "a"})
    assert registry.snapshot()["calls.failed{tenant=a}"] == 1


def test_reset_clears_everything() -> None:
    registry = MetricsRegistry()
    registry.increment("calls.failed")
    registry.observe("turn.response_latency", 1)
    registry.set_gauge("calls.active", 1)
    registry.reset()
    assert registry.snapshot() == {}
