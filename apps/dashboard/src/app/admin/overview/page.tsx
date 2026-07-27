import Link from "next/link";
import { apiFetch, type PlatformOverview } from "@/lib/api";
import { BarList } from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricCard } from "@/components/ui/metric-card";
import { ErrorState } from "@/components/ui/states";

export const metadata = { title: "Platform overview" };

function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

const WARNING_TONE: Record<string, "danger" | "warning"> = {
  calendar_unavailable: "danger",
  post_call_stalled: "warning",
  transfers_unanswered: "danger",
};

export default async function PlatformOverviewPage() {
  const result = await apiFetch<PlatformOverview>("/admin/overview");

  if (!result.ok) {
    return (
      <div className="mx-auto max-w-6xl space-y-4">
        <h1 className="text-xl font-semibold tracking-tight">Platform overview</h1>
        <ErrorState description={result.message} retryHref="/admin/overview" />
      </div>
    );
  }

  const data = result.data;

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Platform overview</h1>
        <p className="text-sm text-muted-foreground">
          Fleet health across every tenant on the platform.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Active tenants" value={data.active_tenants.toLocaleString()} />
        <MetricCard
          label="Calls in progress"
          value={data.active_calls.toLocaleString()}
        />
        <MetricCard
          label="Calls today"
          value={data.calls_today.toLocaleString()}
          hint={`${data.failed_calls_today.toLocaleString()} failed`}
        />
        <MetricCard
          label="Calendar failures"
          value={data.calendar_connection_failures.toLocaleString()}
          hint="Connections needing reconnection"
        />
        <MetricCard
          label="Minutes this month"
          value={data.minutes_this_month.toLocaleString()}
        />
        <MetricCard
          label="Estimated provider cost"
          value={dollars(data.estimated_cost_cents)}
          hint="Month to date, all tenants"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Tenants by status</CardTitle>
          </CardHeader>
          <CardContent>
            <BarList
              points={data.tenants_by_status}
              caption="Tenant count by lifecycle status"
              valueLabel="Tenants"
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Provider errors today</CardTitle>
          </CardHeader>
          <CardContent>
            {data.provider_errors_today.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No provider errors recorded today.
              </p>
            ) : (
              <BarList
                points={data.provider_errors_today}
                caption="Failed calls by failure category, today"
                valueLabel="Calls"
              />
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Tenant readiness warnings</CardTitle>
        </CardHeader>
        <CardContent>
          {data.readiness_warnings.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No tenants need attention right now.
            </p>
          ) : (
            <ul className="space-y-2">
              {data.readiness_warnings.map((warning, index) => (
                <li
                  key={`${warning.tenant_id}-${warning.code}-${index}`}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border p-3"
                >
                  <div>
                    <Link
                      href={`/admin/tenants/${warning.tenant_id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      {warning.tenant_name}
                    </Link>
                    <p className="mt-0.5 text-sm text-muted-foreground">
                      {warning.message}
                    </p>
                  </div>
                  <Badge variant={WARNING_TONE[warning.code] ?? "warning"}>
                    {warning.code.replaceAll("_", " ")}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
