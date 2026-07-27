import Link from "next/link";
import { apiFetch, type AlertStatus, type SystemHealth } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/states";

export const metadata = { title: "System health" };

// Health is a live operational read; a cached page would be worse than
// no page at all.
export const dynamic = "force-dynamic";

const SEVERITY_VARIANT = {
  ok: "success",
  warning: "warning",
  critical: "danger",
} as const;

const OVERALL_COPY = {
  ok: "All systems normal",
  warning: "Needs attention",
  critical: "Action required now",
} as const;

function formatValue(alert: AlertStatus): string {
  if (alert.unit === "ms") return `${Math.round(alert.value)} ms`;
  if (alert.unit === "ratio") return `${Math.round(alert.value * 100)}%`;
  return alert.value.toLocaleString();
}

function formatThreshold(alert: AlertStatus): string {
  const render = (value: number) =>
    alert.unit === "ratio" ? `${Math.round(value * 100)}%` : value.toLocaleString();
  return `warn ${render(alert.warning_at)} · critical ${render(alert.critical_at)}`;
}

function AlertRow({ alert }: { alert: AlertStatus }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-border p-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Badge variant={SEVERITY_VARIANT[alert.severity]}>{alert.severity}</Badge>
          <span className="font-medium">{alert.title}</span>
        </div>
        {alert.detail && (
          <p className="mt-1 text-sm text-muted-foreground">{alert.detail}</p>
        )}
        {alert.severity !== "ok" && (
          <p className="mt-1 text-sm">{alert.runbook}</p>
        )}
      </div>
      <div className="text-right">
        <p className="text-lg font-semibold tabular-nums">{formatValue(alert)}</p>
        <p className="text-xs text-muted-foreground">{formatThreshold(alert)}</p>
        <p className="text-xs text-muted-foreground">last {alert.window_minutes}m</p>
      </div>
    </li>
  );
}

export default async function SystemHealthPage() {
  const result = await apiFetch<SystemHealth>("/admin/system-health");

  if (!result.ok) {
    return (
      <div className="mx-auto max-w-4xl space-y-4">
        <h1 className="text-xl font-semibold tracking-tight">System health</h1>
        <ErrorState description={result.message} retryHref="/admin/system-health" />
      </div>
    );
  }

  const health = result.data;
  // Anything not OK goes first — an operator should not scroll to find
  // the thing that is broken.
  const ordered = [...health.alerts].sort((a, b) => {
    const rank = { critical: 0, warning: 1, ok: 2 } as const;
    return rank[a.severity] - rank[b.severity];
  });
  const firing = ordered.filter((a) => a.severity !== "ok");

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">System health</h1>
          <p className="text-sm text-muted-foreground">
            Checked {new Date(health.checked_at).toLocaleTimeString()} · database{" "}
            {Math.round(health.database_latency_ms)} ms
          </p>
        </div>
        <Badge variant={SEVERITY_VARIANT[health.overall]}>
          {OVERALL_COPY[health.overall]}
        </Badge>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            {firing.length === 0
              ? "No alerts firing"
              : `${firing.length} alert${firing.length === 1 ? "" : "s"} firing`}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            {ordered.map((alert) => (
              <AlertRow key={alert.key} alert={alert} />
            ))}
          </ul>
        </CardContent>
      </Card>

      {health.tenant_failures.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Tenants with repeated failures</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {health.tenant_failures.map((tenant) => (
                <li
                  key={tenant.tenant_id}
                  className="flex items-center justify-between gap-2 rounded-md border border-border p-3"
                >
                  <Link
                    href={`/admin/tenants/${tenant.tenant_id}/calls?outcome=failed`}
                    className="font-medium text-primary hover:underline"
                  >
                    {tenant.tenant_name}
                  </Link>
                  <span className="tabular-nums text-sm text-muted-foreground">
                    {tenant.failed_calls} failed calls
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
