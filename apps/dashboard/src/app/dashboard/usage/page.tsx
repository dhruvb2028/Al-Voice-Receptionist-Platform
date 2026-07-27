import { apiFetch, type TenantOverview } from "@/lib/api";
import { UsageChart } from "@/components/charts/overview-charts";
import { MetricCard } from "@/components/ui/metric-card";
import { ErrorState, PermissionState } from "@/components/ui/states";

export const metadata = { title: "Usage" };

function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export default async function UsagePage() {
  const overview = await apiFetch<TenantOverview>("/tenant/usage");

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Usage</h1>
        <p className="text-sm text-muted-foreground">
          Call volume and estimated provider cost for the current month.
        </p>
      </div>

      {!overview.ok ? (
        overview.status === 403 || overview.status === 404 ? (
          <PermissionState description="Usage is visible to account owners. Ask your owner if you need access." />
        ) : (
          <ErrorState description={overview.message} retryHref="/dashboard/usage" />
        )
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <MetricCard
              label="Minutes this month"
              value={overview.data.metrics.minutes_this_month.toLocaleString()}
            />
            <MetricCard
              label="Estimated provider cost"
              value={dollars(overview.data.metrics.estimated_cost_cents)}
              hint="Platform cost, not your invoice"
            />
            <MetricCard
              label="Calls answered"
              value={overview.data.metrics.calls_answered.toLocaleString()}
              hint={`Last ${overview.data.metrics.window_days} days`}
            />
          </div>
          <UsageChart series={overview.data.series} />
        </>
      )}
    </div>
  );
}
