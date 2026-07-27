"""In-process metrics.

Every metric the platform alerts on is declared here, once, with its
name and unit — so an alert threshold can be read next to the thing it
measures rather than discovered in a dashboard.

Counters and gauges are held in-process and scraped per instance;
histograms keep a bounded reservoir so a long-running instance cannot
grow without limit. This is deliberately small: Cloud Run aggregates
across instances, and the platform's alerting decisions are made from
database state (see ``api.services.health``) rather than from
long-retention time series.
"""

import threading
from bisect import insort
from dataclasses import dataclass, field
from enum import StrEnum


class MetricUnit(StrEnum):
    COUNT = "count"
    MILLISECONDS = "ms"
    SECONDS = "s"
    CENTS = "cents"


@dataclass(frozen=True)
class MetricSpec:
    name: str
    unit: MetricUnit
    description: str


#: The catalog. Adding an alert means adding its metric here first.
METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("calls.active", MetricUnit.COUNT, "Calls currently connected"),
    MetricSpec("calls.completed", MetricUnit.COUNT, "Calls that reached a terminal state"),
    MetricSpec("calls.failed", MetricUnit.COUNT, "Calls that ended in failure"),
    MetricSpec("calls.duration", MetricUnit.SECONDS, "Call duration"),
    MetricSpec("turn.response_latency", MetricUnit.MILLISECONDS, "Caller-to-reply latency"),
    MetricSpec("bookings.failed", MetricUnit.COUNT, "Booking attempts that did not commit"),
    MetricSpec("transfers.failed", MetricUnit.COUNT, "Transfers that never connected"),
    MetricSpec("providers.timeouts", MetricUnit.COUNT, "Provider calls that timed out"),
    MetricSpec("worker.backlog", MetricUnit.COUNT, "Calls awaiting post-call processing"),
    MetricSpec("qstash.failures", MetricUnit.COUNT, "Job deliveries that failed verification"),
    MetricSpec("calendar.failures", MetricUnit.COUNT, "Calendar operations that failed"),
    MetricSpec("database.latency", MetricUnit.MILLISECONDS, "Database round-trip latency"),
    MetricSpec("cache.latency", MetricUnit.MILLISECONDS, "Cache round-trip latency"),
    MetricSpec("providers.cost", MetricUnit.CENTS, "Estimated provider spend"),
    MetricSpec("recordings.upload_failed", MetricUnit.COUNT, "Recording uploads that failed"),
)

METRIC_NAMES = frozenset(spec.name for spec in METRICS)

#: Samples retained per histogram. Enough for a stable p95 on one
#: instance, small enough that memory is bounded.
RESERVOIR_SIZE = 512


def _percentile_of(samples: list[float], fraction: float) -> float | None:
    """Linear-interpolated percentile of an already-sorted reservoir."""
    if not samples:
        return None
    if len(samples) == 1:
        return samples[0]
    position = fraction * (len(samples) - 1)
    low = int(position)
    high = min(low + 1, len(samples) - 1)
    weight = position - low
    return samples[low] * (1 - weight) + samples[high] * weight


@dataclass
class MetricsRegistry:
    """Counters, gauges, and histograms for one process."""

    _counters: dict[tuple[str, str], float] = field(default_factory=dict)
    _gauges: dict[tuple[str, str], float] = field(default_factory=dict)
    _histograms: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None) -> tuple[str, str]:
        if not labels:
            return (name, "")
        rendered = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return (name, rendered)

    def increment(
        self, name: str, value: float = 1.0, labels: dict[str, str] | None = None
    ) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            samples = self._histograms.setdefault(key, [])
            insort(samples, value)
            if len(samples) > RESERVOIR_SIZE:
                # Drop the median rather than an extreme: tail latency is
                # the thing worth alerting on, so keep the tails intact.
                del samples[len(samples) // 2]

    def counter(self, name: str, labels: dict[str, str] | None = None) -> float:
        with self._lock:
            return self._counters.get(self._key(name, labels), 0.0)

    def gauge(self, name: str, labels: dict[str, str] | None = None) -> float | None:
        with self._lock:
            return self._gauges.get(self._key(name, labels))

    def percentile(
        self, name: str, fraction: float, labels: dict[str, str] | None = None
    ) -> float | None:
        with self._lock:
            samples = list(self._histograms.get(self._key(name, labels), []))
        return _percentile_of(samples, fraction)

    def snapshot(self) -> dict[str, float]:
        """Flat name→value view for scraping and debugging."""
        out: dict[str, float] = {}
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {k: list(v) for k, v in self._histograms.items()}

        for (name, labels), value in {**counters, **gauges}.items():
            out[f"{name}{{{labels}}}" if labels else name] = value
        for (name, labels), samples in histograms.items():
            if not samples:
                continue
            suffix = f"{{{labels}}}" if labels else ""
            out[f"{name}.count{suffix}"] = float(len(samples))
            out[f"{name}.p50{suffix}"] = _percentile_of(samples, 0.5) or 0.0
            out[f"{name}.p95{suffix}"] = _percentile_of(samples, 0.95) or 0.0
        return out

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


_registry = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    return _registry


def increment(name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
    _registry.increment(name, value, labels)


def observe(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    _registry.observe(name, value, labels)


def set_gauge(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    _registry.set_gauge(name, value, labels)


__all__ = [
    "METRICS",
    "METRIC_NAMES",
    "RESERVOIR_SIZE",
    "MetricSpec",
    "MetricUnit",
    "MetricsRegistry",
    "get_registry",
    "increment",
    "observe",
    "set_gauge",
]
