import { apiFetch, type TenantOverview } from "@/lib/api";
import { OverviewCharts } from "@/components/charts/overview-charts";
import { MetricCard } from "@/components/ui/metric-card";
import { ErrorState } from "@/components/ui/states";

export const metadata = { title: "Overview" };

interface TenantResponse {
  id: string;
  name: string;
  status: string;
  timezone: string;
}

function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}m ${whole % 60}s`;
}

function millis(value: number | null): string {
  return value === null ? "—" : `${Math.round(value)} ms`;
}

function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export default async function OverviewPage() {
  const [tenant, overview] = await Promise.all([
    apiFetch<TenantResponse>("/tenant"),
    apiFetch<TenantOverview>("/tenant/overview"),
  ]);

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">
          {tenant.ok
            ? `Live activity for ${tenant.data.name}, last 30 days.`
            : "Live activity for your business, last 30 days."}
        </p>
      </div>

      {!overview.ok ? (
        <ErrorState description={overview.message} retryHref="/dashboard" />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="Calls answered"
              value={overview.data.metrics.calls_answered.toLocaleString()}
              hint={`${overview.data.metrics.calls_after_hours.toLocaleString()} after hours`}
            />
            <MetricCard
              label="Appointments booked"
              value={overview.data.metrics.appointments_booked.toLocaleString()}
            />
            <MetricCard
              label="Messages captured"
              value={overview.data.metrics.messages_captured.toLocaleString()}
            />
            <MetricCard
              label="Handled without a human"
              value={percent(overview.data.metrics.containment_rate)}
              hint={
                overview.data.metrics.containment_rate === null
                  ? "No calls yet"
                  : `${overview.data.metrics.calls_transferred.toLocaleString()} transferred`
              }
            />
            <MetricCard
              label="Average call length"
              value={duration(overview.data.metrics.average_call_seconds)}
            />
            <MetricCard
              label="Response latency (median)"
              value={millis(overview.data.metrics.latency_p50_ms)}
              hint={`95th percentile ${millis(overview.data.metrics.latency_p95_ms)}`}
            />
            <MetricCard
              label="Failed calls"
              value={overview.data.metrics.calls_failed.toLocaleString()}
            />
            <MetricCard
              label="Minutes this month"
              value={overview.data.metrics.minutes_this_month.toLocaleString()}
              hint={`${dollars(overview.data.metrics.estimated_cost_cents)} estimated cost`}
            />
          </div>

          {overview.data.metrics.recovered_revenue && (
            <div className="rounded-lg border border-border bg-card p-4">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Estimated value of booked work
              </p>
              <p className="mt-1 text-2xl font-semibold tracking-tight">
                {dollars(overview.data.metrics.recovered_revenue.amount_cents)}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                An estimate:{" "}
                {overview.data.metrics.recovered_revenue.bookings_counted.toLocaleString()}{" "}
                booking(s) × the{" "}
                {dollars(overview.data.metrics.recovered_revenue.average_job_value_cents)}{" "}
                average job value you provided. It is not measured revenue.
              </p>
            </div>
          )}

          <OverviewCharts series={overview.data.series} />
        </>
      )}
    </div>
  );
}
