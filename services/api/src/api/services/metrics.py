"""Overview metrics for the client dashboard and the platform console.

Every number here is computed from persisted rows — nothing is estimated
or interpolated. Two rules govern this module:

* Recovered revenue is never fabricated. It is returned only when the
  tenant has supplied an average job value, and always carries the
  ``is_estimate`` flag so the UI can label it.
* A metric with no underlying data is ``None``, not zero. "We have not
  measured this yet" and "this happened zero times" are different facts
  and the dashboard renders them differently.
"""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ai_database.enums import (
    BookingStatus,
    CalendarConnectionStatus,
    CallOutcome,
    ProcessingStatus,
    TenantStatus,
)
from ai_database.models import (
    Booking,
    BusinessHours,
    CalendarConnection,
    Call,
    Escalation,
    Message,
    Tenant,
    TenantConfig,
    Turn,
    UsageRecord,
)
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

#: outcomes the receptionist handled end to end, without a human
CONTAINED_OUTCOMES = (
    CallOutcome.BOOKED,
    CallOutcome.MESSAGE_TAKEN,
    CallOutcome.ANSWERED_INQUIRY,
)

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 365


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _as_int(value: Any) -> int:
    """COALESCE(SUM(...), 0) still types as optional; empty means zero."""
    return int(value) if value is not None else 0


def _tz(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


class MetricValue(BaseModel):
    """A single headline metric. ``value`` of None means not measured."""

    value: float | None
    unit: str = "count"


class RevenueEstimate(BaseModel):
    """Only ever populated from a tenant-supplied average job value."""

    amount_cents: int
    bookings_counted: int
    average_job_value_cents: int
    is_estimate: bool = True


class SeriesPoint(BaseModel):
    label: str
    value: float


class OverviewMetrics(BaseModel):
    window_days: int
    calls_answered: int
    calls_after_hours: int
    appointments_booked: int
    messages_captured: int
    calls_transferred: int
    calls_failed: int
    containment_rate: float | None
    average_call_seconds: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    minutes_this_month: int
    estimated_cost_cents: int
    #: absent unless the tenant supplied an average job value
    recovered_revenue: RevenueEstimate | None = None


class OverviewSeries(BaseModel):
    calls_over_time: list[SeriesPoint]
    outcomes: list[SeriesPoint]
    calls_by_hour: list[SeriesPoint]
    bookings_over_time: list[SeriesPoint]
    urgency_distribution: list[SeriesPoint]
    latency_trend: list[SeriesPoint]
    usage_trend: list[SeriesPoint]


class TenantOverview(BaseModel):
    metrics: OverviewMetrics
    series: OverviewSeries


async def _after_hours_count(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    since: datetime,
    until: datetime,
    timezone: str | None,
) -> int:
    """Calls answered outside the tenant's configured opening hours.

    Comparison happens in the tenant's local time — an 8pm call is
    after-hours for the business, whatever UTC says.
    """
    hours = {
        h.weekday: h
        for h in (
            await session.execute(select(BusinessHours).where(BusinessHours.tenant_id == tenant_id))
        )
        .scalars()
        .all()
    }
    if not hours:
        return 0

    started = (
        (
            await session.execute(
                select(Call.started_at).where(
                    Call.tenant_id == tenant_id,
                    Call.started_at >= since,
                    Call.started_at <= until,
                )
            )
        )
        .scalars()
        .all()
    )
    zone = _tz(timezone)
    count = 0
    for started_at in started:
        local = started_at.astimezone(zone) if zone else started_at
        rule = hours.get(local.weekday())
        if rule is None or rule.closed:
            count += 1
            continue
        if rule.opens_at is None or rule.closes_at is None:
            continue
        if not (rule.opens_at <= local.time() < rule.closes_at):
            count += 1
    return count


async def _latency_percentiles(
    session: AsyncSession, tenant_id: uuid.UUID, since: datetime, until: datetime
) -> tuple[float | None, float | None]:
    """p50/p95 of end-to-end response latency, computed in PostgreSQL."""
    row = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(Turn.total_latency_ms),
                func.percentile_cont(0.95).within_group(Turn.total_latency_ms),
            ).where(
                Turn.tenant_id == tenant_id,
                Turn.total_latency_ms.is_not(None),
                Turn.created_at >= since,
                Turn.created_at <= until,
            )
        )
    ).one()
    p50 = float(row[0]) if row[0] is not None else None
    p95 = float(row[1]) if row[1] is not None else None
    return p50, p95


def _fill_daily(rows: dict[date, float], start: date, end: date) -> list[SeriesPoint]:
    """Dense daily series — gaps become explicit zeroes so the chart does
    not imply activity across a missing day."""
    points: list[SeriesPoint] = []
    cursor = start
    while cursor <= end:
        points.append(SeriesPoint(label=cursor.isoformat(), value=rows.get(cursor, 0.0)))
        cursor += timedelta(days=1)
    return points


async def tenant_overview(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> TenantOverview:
    now = now or datetime.now(UTC)
    window_days = max(1, min(window_days, MAX_WINDOW_DAYS))
    since = now - timedelta(days=window_days)
    month0 = _month_start(now)

    config = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    ).scalar_one_or_none()
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    timezone = (config.timezone if config else None) or (tenant.timezone if tenant else None)

    counts = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(Call.outcome == CallOutcome.TRANSFERRED),
                func.count().filter(Call.outcome == CallOutcome.FAILED),
                func.count().filter(Call.outcome.in_(CONTAINED_OUTCOMES)),
                func.avg(Call.duration_seconds),
            ).where(
                Call.tenant_id == tenant_id,
                Call.started_at >= since,
                Call.started_at <= now,
            )
        )
    ).one()
    calls_answered = int(counts[0])
    transferred = int(counts[1])
    failed = int(counts[2])
    contained = int(counts[3])
    average_seconds = float(counts[4]) if counts[4] is not None else None

    booked = (
        await session.execute(
            select(func.count()).where(
                Booking.tenant_id == tenant_id,
                Booking.status == BookingStatus.CONFIRMED,
                Booking.created_at >= since,
                Booking.created_at <= now,
            )
        )
    ).scalar_one()
    messages = (
        await session.execute(
            select(func.count()).where(
                Message.tenant_id == tenant_id,
                Message.created_at >= since,
                Message.created_at <= now,
            )
        )
    ).scalar_one()

    usage_row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(UsageRecord.quantity).filter(UsageRecord.usage_type == "call_minutes"),
                    0,
                ),
            ).where(
                UsageRecord.tenant_id == tenant_id,
                UsageRecord.recorded_at >= month0,
                UsageRecord.recorded_at <= now,
            )
        )
    ).one()
    minutes_this_month = _as_int(usage_row[0])
    estimated_cost_cents = _as_int(
        (
            await session.execute(
                select(func.coalesce(func.sum(Call.estimated_cost_cents), 0)).where(
                    Call.tenant_id == tenant_id,
                    Call.started_at >= month0,
                    Call.started_at <= now,
                )
            )
        ).scalar_one()
    )

    after_hours = await _after_hours_count(session, tenant_id, since, now, timezone)
    p50, p95 = await _latency_percentiles(session, tenant_id, since, now)

    # Containment is undefined with no calls — None, not a misleading 0%.
    containment = (contained / calls_answered) if calls_answered else None

    recovered: RevenueEstimate | None = None
    average_job_value = config.average_job_value_cents if config else None
    if average_job_value:
        recovered = RevenueEstimate(
            amount_cents=average_job_value * int(booked),
            bookings_counted=int(booked),
            average_job_value_cents=average_job_value,
        )

    metrics = OverviewMetrics(
        window_days=window_days,
        calls_answered=calls_answered,
        calls_after_hours=after_hours,
        appointments_booked=int(booked),
        messages_captured=int(messages),
        calls_transferred=transferred,
        calls_failed=failed,
        containment_rate=containment,
        average_call_seconds=average_seconds,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        minutes_this_month=minutes_this_month,
        estimated_cost_cents=estimated_cost_cents,
        recovered_revenue=recovered,
    )
    series = await _tenant_series(session, tenant_id, since=since, now=now, timezone=timezone)
    return TenantOverview(metrics=metrics, series=series)


async def _tenant_series(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    since: datetime,
    now: datetime,
    timezone: str | None,
) -> OverviewSeries:
    zone = _tz(timezone) or UTC

    call_rows = (
        await session.execute(
            select(Call.started_at, Call.outcome).where(
                Call.tenant_id == tenant_id,
                Call.started_at >= since,
                Call.started_at <= now,
            )
        )
    ).all()
    per_day: dict[date, float] = defaultdict(float)
    per_hour: dict[int, float] = defaultdict(float)
    per_outcome: dict[str, float] = defaultdict(float)
    for started_at, outcome in call_rows:
        local = started_at.astimezone(zone)
        per_day[local.date()] += 1
        per_hour[local.hour] += 1
        per_outcome[outcome.value if outcome else "in_progress"] += 1

    booking_rows = (
        (
            await session.execute(
                select(Booking.created_at).where(
                    Booking.tenant_id == tenant_id,
                    Booking.status == BookingStatus.CONFIRMED,
                    Booking.created_at >= since,
                    Booking.created_at <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    bookings_per_day: dict[date, float] = defaultdict(float)
    for created_at in booking_rows:
        bookings_per_day[created_at.astimezone(zone).date()] += 1

    urgency_rows = (
        await session.execute(
            select(Message.urgency, func.count())
            .where(
                Message.tenant_id == tenant_id,
                Message.created_at >= since,
                Message.created_at <= now,
            )
            .group_by(Message.urgency)
        )
    ).all()

    latency_day = func.date_trunc("day", func.timezone(str(zone), Turn.created_at)).label("day")
    latency_rows = (
        await session.execute(
            select(
                latency_day,
                func.percentile_cont(0.5).within_group(Turn.total_latency_ms),
            )
            .where(
                Turn.tenant_id == tenant_id,
                Turn.total_latency_ms.is_not(None),
                Turn.created_at >= since,
                Turn.created_at <= now,
            )
            .group_by(latency_day)
            .order_by(latency_day)
        )
    ).all()

    usage_day = func.date_trunc("day", func.timezone(str(zone), UsageRecord.recorded_at)).label(
        "day"
    )
    usage_rows = (
        await session.execute(
            select(usage_day, func.coalesce(func.sum(UsageRecord.quantity), 0))
            .where(
                UsageRecord.tenant_id == tenant_id,
                UsageRecord.usage_type == "call_minutes",
                UsageRecord.recorded_at >= since,
                UsageRecord.recorded_at <= now,
            )
            .group_by(usage_day)
            .order_by(usage_day)
        )
    ).all()

    start_day, end_day = since.astimezone(zone).date(), now.astimezone(zone).date()
    return OverviewSeries(
        calls_over_time=_fill_daily(per_day, start_day, end_day),
        outcomes=[
            SeriesPoint(label=name, value=value)
            for name, value in sorted(per_outcome.items(), key=lambda kv: -kv[1])
        ],
        calls_by_hour=[
            SeriesPoint(label=f"{hour:02d}", value=per_hour.get(hour, 0.0)) for hour in range(24)
        ],
        bookings_over_time=_fill_daily(bookings_per_day, start_day, end_day),
        urgency_distribution=[
            SeriesPoint(label=urgency.value, value=float(count)) for urgency, count in urgency_rows
        ],
        latency_trend=[
            SeriesPoint(label=_as_date(day).isoformat(), value=float(value))
            for day, value in latency_rows
            if value is not None
        ],
        usage_trend=[
            SeriesPoint(label=_as_date(day).isoformat(), value=float(value))
            for day, value in usage_rows
        ],
    )


def _as_date(value: Any) -> date:
    return value.date() if isinstance(value, datetime) else value


# --- Platform overview -------------------------------------------------------


class TenantWarning(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    code: str
    message: str


class PlatformOverview(BaseModel):
    active_tenants: int
    tenants_by_status: list[SeriesPoint]
    active_calls: int
    failed_calls_today: int
    calls_today: int
    minutes_this_month: int
    estimated_cost_cents: int
    calendar_connection_failures: int
    provider_errors_today: list[SeriesPoint]
    readiness_warnings: list[TenantWarning]


async def platform_overview(
    session: AsyncSession, *, now: datetime | None = None
) -> PlatformOverview:
    now = now or datetime.now(UTC)
    day0 = datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)
    month0 = _month_start(now)

    status_rows = (
        await session.execute(
            select(Tenant.status, func.count())
            .where(Tenant.archived_at.is_(None))
            .group_by(Tenant.status)
        )
    ).all()
    active_tenants = sum(count for status, count in status_rows if status is TenantStatus.ACTIVE)

    call_row = (
        await session.execute(
            select(
                func.count().filter(Call.started_at >= day0),
                func.count().filter(Call.started_at >= day0, Call.outcome == CallOutcome.FAILED),
                # "Active" = started, not yet ended.
                func.count().filter(Call.ended_at.is_(None)),
            ).where(Call.started_at >= month0)
        )
    ).one()

    minutes = _as_int(
        (
            await session.execute(
                select(func.coalesce(func.sum(UsageRecord.quantity), 0)).where(
                    UsageRecord.usage_type == "call_minutes",
                    UsageRecord.recorded_at >= month0,
                )
            )
        ).scalar_one()
    )
    cost = _as_int(
        (
            await session.execute(
                select(func.coalesce(func.sum(Call.estimated_cost_cents), 0)).where(
                    Call.started_at >= month0
                )
            )
        ).scalar_one()
    )
    calendar_failures = int(
        (
            await session.execute(
                select(func.count()).where(
                    CalendarConnection.status.in_(
                        [
                            CalendarConnectionStatus.EXPIRED,
                            CalendarConnectionStatus.REVOKED,
                            CalendarConnectionStatus.ERROR,
                        ]
                    )
                )
            )
        ).scalar_one()
    )
    error_rows = (
        await session.execute(
            select(Call.failure_category, func.count())
            .where(Call.started_at >= day0, Call.failure_category.is_not(None))
            .group_by(Call.failure_category)
            .order_by(func.count().desc())
        )
    ).all()

    return PlatformOverview(
        active_tenants=active_tenants,
        tenants_by_status=[
            SeriesPoint(label=status.value, value=float(count)) for status, count in status_rows
        ],
        active_calls=int(call_row[2]),
        failed_calls_today=int(call_row[1]),
        calls_today=int(call_row[0]),
        minutes_this_month=minutes,
        estimated_cost_cents=cost,
        calendar_connection_failures=calendar_failures,
        provider_errors_today=[
            SeriesPoint(label=str(category), value=float(count)) for category, count in error_rows
        ],
        readiness_warnings=await _readiness_warnings(session, now=now),
    )


async def _readiness_warnings(session: AsyncSession, *, now: datetime) -> list[TenantWarning]:
    """Operational warnings across live tenants: unreachable calendars,
    stalled post-call processing, and unanswered escalations."""
    warnings: list[TenantWarning] = []
    names = {
        t.id: t.name
        for t in (
            await session.execute(
                select(Tenant).where(
                    Tenant.archived_at.is_(None),
                    Tenant.status.in_([TenantStatus.ACTIVE, TenantStatus.TESTING]),
                )
            )
        ).scalars()
    }
    if not names:
        return warnings

    broken_calendars = (
        await session.execute(
            select(CalendarConnection.tenant_id, CalendarConnection.status).where(
                CalendarConnection.tenant_id.in_(names),
                CalendarConnection.status != CalendarConnectionStatus.CONNECTED,
            )
        )
    ).all()
    for tenant_id, status in broken_calendars:
        warnings.append(
            TenantWarning(
                tenant_id=tenant_id,
                tenant_name=names[tenant_id],
                code="calendar_unavailable",
                message=f"Calendar connection is {status.value}; bookings cannot be written.",
            )
        )

    stalled_cutoff = now - timedelta(hours=6)
    stalled = (
        await session.execute(
            select(Call.tenant_id, func.count())
            .where(
                Call.tenant_id.in_(names),
                Call.post_processing_status.in_(
                    [ProcessingStatus.PENDING, ProcessingStatus.FAILED]
                ),
                Call.ended_at.is_not(None),
                Call.ended_at < stalled_cutoff,
            )
            .group_by(Call.tenant_id)
        )
    ).all()
    for tenant_id, count in stalled:
        warnings.append(
            TenantWarning(
                tenant_id=tenant_id,
                tenant_name=names[tenant_id],
                code="post_call_stalled",
                message=f"{count} call(s) have not finished post-call processing.",
            )
        )

    unconnected = (
        await session.execute(
            select(Escalation.tenant_id, func.count())
            .where(
                Escalation.tenant_id.in_(names),
                Escalation.connected_at.is_(None),
                Escalation.initiated_at >= now - timedelta(days=1),
            )
            .group_by(Escalation.tenant_id)
        )
    ).all()
    for tenant_id, count in unconnected:
        warnings.append(
            TenantWarning(
                tenant_id=tenant_id,
                tenant_name=names[tenant_id],
                code="transfers_unanswered",
                message=f"{count} transfer(s) in the last day never connected to a human.",
            )
        )

    return warnings


__all__ = [
    "MetricValue",
    "OverviewMetrics",
    "OverviewSeries",
    "PlatformOverview",
    "RevenueEstimate",
    "SeriesPoint",
    "TenantOverview",
    "TenantWarning",
    "platform_overview",
    "tenant_overview",
]
