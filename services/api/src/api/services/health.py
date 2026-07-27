"""System health: alert definitions and their live evaluation.

Alerts are computed from persisted state rather than from an in-process
metrics registry, because the thing an operator needs to know is "are
calls failing *now*", and that answer must survive an instance restart
and be identical on every replica.

Each alert declares its own threshold and window next to the query that
evaluates it, so a threshold can be reviewed without reading a
dashboard configuration elsewhere.
"""

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ai_database.enums import (
    BookingStatus,
    CalendarConnectionStatus,
    CallOutcome,
    NotificationStatus,
    ProcessingStatus,
    RecordingStatus,
)
from ai_database.models import (
    Booking,
    CalendarConnection,
    Call,
    Escalation,
    NotificationDelivery,
    Tenant,
)
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class AlertSeverity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class AlertSpec:
    """One alert: what it watches, and when it fires."""

    key: str
    title: str
    #: what an operator should do when this fires
    runbook: str
    warning_at: float
    critical_at: float
    window_minutes: int = 60
    unit: str = "count"


ALERTS: tuple[AlertSpec, ...] = (
    AlertSpec(
        key="call_failure_spike",
        title="Call failures",
        runbook="Check provider status and the failure categories on recent calls.",
        warning_at=3,
        critical_at=10,
    ),
    AlertSpec(
        key="latency_p95_regression",
        title="p95 response latency",
        runbook="Check Groq and Cartesia latency; consider the fallback model.",
        warning_at=2500,
        critical_at=4000,
        unit="ms",
    ),
    AlertSpec(
        key="booking_failures",
        title="Booking failures",
        runbook="Check the calendar connection and reconciliation queue.",
        warning_at=2,
        critical_at=5,
    ),
    AlertSpec(
        key="calendar_outage",
        title="Calendar connections down",
        runbook="Tenants must reconnect Google Calendar from integrations.",
        warning_at=1,
        critical_at=3,
    ),
    AlertSpec(
        key="transfer_failures",
        title="Transfers that never connected",
        runbook="Verify the escalation number answers; confirm message fallback fired.",
        warning_at=2,
        critical_at=5,
    ),
    AlertSpec(
        key="provider_errors",
        title="Provider errors",
        runbook="Identify the provider from failure_category; check its status page.",
        warning_at=5,
        critical_at=15,
    ),
    AlertSpec(
        key="voice_saturation",
        title="Concurrent call saturation",
        runbook="Capacity cap reached; callers are being turned away.",
        warning_at=0.8,
        critical_at=1.0,
        unit="ratio",
        window_minutes=5,
    ),
    AlertSpec(
        key="worker_backlog",
        title="Post-call backlog",
        runbook="Check QStash delivery and worker logs; jobs may be dead-lettering.",
        warning_at=5,
        critical_at=20,
        window_minutes=360,
    ),
    AlertSpec(
        key="recording_upload_failures",
        title="Recording upload failures",
        runbook="Check R2 credentials and bucket policy.",
        warning_at=1,
        critical_at=5,
    ),
    AlertSpec(
        key="notification_failures",
        title="Notification delivery failures",
        runbook="Check Resend and Twilio credentials and delivery callbacks.",
        warning_at=3,
        critical_at=10,
    ),
    AlertSpec(
        key="database_latency",
        title="Database latency",
        runbook="Check Neon status and connection pool saturation.",
        warning_at=250,
        critical_at=1000,
        unit="ms",
        window_minutes=1,
    ),
    AlertSpec(
        key="tenant_repeated_failures",
        title="Tenants with repeated failures",
        runbook="A single tenant failing repeatedly usually means its own configuration.",
        warning_at=1,
        critical_at=3,
    ),
)

ALERTS_BY_KEY = {alert.key: alert for alert in ALERTS}


class AlertStatus(BaseModel):
    key: str
    title: str
    severity: AlertSeverity
    value: float
    warning_at: float
    critical_at: float
    unit: str
    window_minutes: int
    runbook: str
    detail: str = ""


class TenantFailure(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    failed_calls: int


class SystemHealth(BaseModel):
    checked_at: datetime
    overall: AlertSeverity
    alerts: list[AlertStatus]
    tenant_failures: list[TenantFailure]
    database_latency_ms: float


def _severity(spec: AlertSpec, value: float) -> AlertSeverity:
    if value >= spec.critical_at:
        return AlertSeverity.CRITICAL
    if value >= spec.warning_at:
        return AlertSeverity.WARNING
    return AlertSeverity.OK


def _status(spec: AlertSpec, value: float, detail: str = "") -> AlertStatus:
    return AlertStatus(
        key=spec.key,
        title=spec.title,
        severity=_severity(spec, value),
        value=round(value, 2),
        warning_at=spec.warning_at,
        critical_at=spec.critical_at,
        unit=spec.unit,
        window_minutes=spec.window_minutes,
        runbook=spec.runbook,
        detail=detail,
    )


def _window(now: datetime, spec: AlertSpec) -> datetime:
    return now - timedelta(minutes=spec.window_minutes)


async def system_health(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    max_concurrent_calls: int = 6,
) -> SystemHealth:
    """Evaluate every alert against current state."""
    now = now or datetime.now(UTC)
    statuses: list[AlertStatus] = []

    # Database latency is measured by the probe itself, so a slow
    # database shows up here rather than only as a timeout elsewhere.
    started = time.perf_counter()
    await session.execute(select(func.count(Tenant.id)))
    database_latency_ms = (time.perf_counter() - started) * 1000

    spec = ALERTS_BY_KEY["call_failure_spike"]
    failures = int(
        (
            await session.execute(
                select(func.count(Call.id)).where(
                    Call.started_at >= _window(now, spec),
                    Call.outcome == CallOutcome.FAILED,
                )
            )
        ).scalar_one()
    )
    statuses.append(_status(spec, failures))

    spec = ALERTS_BY_KEY["latency_p95_regression"]
    from ai_database.models import Turn

    p95 = (
        await session.execute(
            select(func.percentile_cont(0.95).within_group(Turn.total_latency_ms)).where(
                Turn.created_at >= _window(now, spec), Turn.total_latency_ms.is_not(None)
            )
        )
    ).scalar_one()
    statuses.append(
        _status(spec, float(p95) if p95 is not None else 0.0, "" if p95 else "no turns measured")
    )

    spec = ALERTS_BY_KEY["booking_failures"]
    booking_failures = int(
        (
            await session.execute(
                select(func.count(Booking.id)).where(
                    Booking.created_at >= _window(now, spec),
                    Booking.status.in_(
                        [BookingStatus.FAILED, BookingStatus.RECONCILIATION_REQUIRED]
                    ),
                )
            )
        ).scalar_one()
    )
    statuses.append(_status(spec, booking_failures))

    spec = ALERTS_BY_KEY["calendar_outage"]
    calendar_down = int(
        (
            await session.execute(
                select(func.count(CalendarConnection.id)).where(
                    CalendarConnection.status != CalendarConnectionStatus.CONNECTED
                )
            )
        ).scalar_one()
    )
    statuses.append(_status(spec, calendar_down))

    spec = ALERTS_BY_KEY["transfer_failures"]
    transfer_failures = int(
        (
            await session.execute(
                select(func.count(Escalation.id)).where(
                    Escalation.initiated_at >= _window(now, spec),
                    Escalation.connected_at.is_(None),
                )
            )
        ).scalar_one()
    )
    statuses.append(_status(spec, transfer_failures))

    spec = ALERTS_BY_KEY["provider_errors"]
    provider_rows = (
        await session.execute(
            select(Call.failure_category, func.count(Call.id))
            .where(
                Call.started_at >= _window(now, spec),
                Call.failure_category.is_not(None),
            )
            .group_by(Call.failure_category)
            .order_by(func.count(Call.id).desc())
        )
    ).all()
    provider_total = sum(int(count) for _, count in provider_rows)
    top = ", ".join(f"{category}={count}" for category, count in provider_rows[:3])
    statuses.append(_status(spec, provider_total, top))

    spec = ALERTS_BY_KEY["voice_saturation"]
    active = int(
        (
            await session.execute(
                select(func.count(Call.id)).where(
                    Call.ended_at.is_(None), Call.started_at >= now - timedelta(hours=2)
                )
            )
        ).scalar_one()
    )
    ratio = active / max_concurrent_calls if max_concurrent_calls else 0.0
    statuses.append(_status(spec, ratio, f"{active} active of {max_concurrent_calls}"))

    spec = ALERTS_BY_KEY["worker_backlog"]
    backlog = int(
        (
            await session.execute(
                select(func.count(Call.id)).where(
                    Call.ended_at.is_not(None),
                    Call.ended_at < _window(now, spec),
                    Call.post_processing_status.in_(
                        [ProcessingStatus.PENDING, ProcessingStatus.FAILED]
                    ),
                )
            )
        ).scalar_one()
    )
    statuses.append(_status(spec, backlog))

    spec = ALERTS_BY_KEY["recording_upload_failures"]
    upload_failures = int(
        (
            await session.execute(
                select(func.count(Call.id)).where(
                    Call.started_at >= _window(now, spec),
                    Call.recording_status == RecordingStatus.FAILED,
                )
            )
        ).scalar_one()
    )
    statuses.append(_status(spec, upload_failures))

    spec = ALERTS_BY_KEY["notification_failures"]
    notification_failures = int(
        (
            await session.execute(
                select(func.count(NotificationDelivery.id)).where(
                    NotificationDelivery.created_at >= _window(now, spec),
                    NotificationDelivery.status == NotificationStatus.FAILED,
                )
            )
        ).scalar_one()
    )
    statuses.append(_status(spec, notification_failures))

    statuses.append(_status(ALERTS_BY_KEY["database_latency"], database_latency_ms))

    spec = ALERTS_BY_KEY["tenant_repeated_failures"]
    tenant_rows = (
        await session.execute(
            select(Call.tenant_id, Tenant.name, func.count(Call.id).label("failures"))
            .join(Tenant, Tenant.id == Call.tenant_id)
            .where(
                Call.started_at >= _window(now, spec),
                Call.outcome == CallOutcome.FAILED,
            )
            .group_by(Call.tenant_id, Tenant.name)
            .having(func.count(Call.id) >= 3)
            .order_by(func.count(Call.id).desc())
        )
    ).all()
    tenant_failures = [
        TenantFailure(tenant_id=row[0], tenant_name=row[1], failed_calls=int(row[2]))
        for row in tenant_rows
    ]
    statuses.append(
        _status(
            spec,
            len(tenant_failures),
            ", ".join(f"{t.tenant_name} ({t.failed_calls})" for t in tenant_failures[:3]),
        )
    )

    overall = AlertSeverity.OK
    if any(s.severity is AlertSeverity.CRITICAL for s in statuses):
        overall = AlertSeverity.CRITICAL
    elif any(s.severity is AlertSeverity.WARNING for s in statuses):
        overall = AlertSeverity.WARNING

    return SystemHealth(
        checked_at=now,
        overall=overall,
        alerts=statuses,
        tenant_failures=tenant_failures,
        database_latency_ms=round(database_latency_ms, 2),
    )


__all__ = [
    "ALERTS",
    "ALERTS_BY_KEY",
    "AlertSeverity",
    "AlertSpec",
    "AlertStatus",
    "SystemHealth",
    "TenantFailure",
    "system_health",
]
