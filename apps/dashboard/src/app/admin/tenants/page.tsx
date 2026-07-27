import Link from "next/link";
import { apiFetch, type TenantListResponse } from "@/lib/api";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { TenantListToolbar } from "./toolbar";

export const metadata = { title: "Tenants" };

const STATUSES = ["onboarding", "testing", "active", "paused", "suspended", "churned"];

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default async function TenantsPage({
  searchParams,
}: {
  searchParams: Promise<{ search?: string; status?: string; sort?: string; page?: string }>;
}) {
  const params = await searchParams;
  const page = Math.max(1, Number(params.page) || 1);
  const result = await apiFetch<TenantListResponse>("/admin/tenants", {
    searchParams: {
      search: params.search,
      status: params.status,
      sort: params.sort,
      page: String(page),
    },
  });

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Tenants</h1>
          <p className="text-sm text-muted-foreground">
            Every business on the platform, with live operational health.
          </p>
        </div>
        <Link href="/admin/tenants/new">
          <Button>New tenant</Button>
        </Link>
      </div>

      <TenantListToolbar statuses={STATUSES} />

      {!result.ok ? (
        result.status === 401 || result.status === 404 ? (
          <Card className="p-8 text-center">
            <p className="text-sm font-medium">You don&apos;t have access to this area.</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Platform-admin permissions are required. If you believe this is a mistake,
              contact the platform operator.
            </p>
          </Card>
        ) : (
          <Card className="p-8 text-center" role="alert">
            <p className="text-sm font-medium">Something went wrong loading tenants.</p>
            <p className="mt-1 text-sm text-muted-foreground">{result.message}</p>
            <Link href="/admin/tenants" className="mt-3 inline-block">
              <Button variant="secondary" size="sm">
                Try again
              </Button>
            </Link>
          </Card>
        )
      ) : result.data.items.length === 0 ? (
        <Card className="p-10 text-center">
          <p className="text-sm font-medium">No tenants found</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {params.search || params.status
              ? "No tenants match the current filters."
              : "Create the first tenant to get started."}
          </p>
          {!params.search && !params.status && (
            <Link href="/admin/tenants/new" className="mt-4 inline-block">
              <Button size="sm">Create tenant</Button>
            </Link>
          )}
        </Card>
      ) : (
        <>
          <Card className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="px-4 py-2.5 font-medium">Tenant</th>
                  <th className="px-2 py-2.5 font-medium">Status</th>
                  <th className="px-2 py-2.5 text-right font-medium">Numbers</th>
                  <th className="px-2 py-2.5 text-right font-medium">Today</th>
                  <th className="px-2 py-2.5 text-right font-medium">Month</th>
                  <th className="px-2 py-2.5 text-right font-medium">Failed</th>
                  <th className="px-2 py-2.5 font-medium">Calendar</th>
                  <th className="px-2 py-2.5 font-medium">Last success</th>
                  <th className="px-2 py-2.5 text-right font-medium">Minutes</th>
                  <th className="px-4 py-2.5 font-medium">Ready</th>
                </tr>
              </thead>
              <tbody>
                {result.data.items.map((tenant) => (
                  <tr
                    key={tenant.id}
                    className="border-b border-border last:border-0 hover:bg-muted/50"
                  >
                    <td className="px-4 py-2.5">
                      <Link
                        href={`/admin/tenants/${tenant.id}`}
                        className="font-medium text-primary hover:underline"
                      >
                        {tenant.name}
                      </Link>
                      <span className="ml-2 text-xs text-muted-foreground">
                        {tenant.vertical}
                      </span>
                    </td>
                    <td className="px-2 py-2.5">
                      <Badge variant={statusVariant(tenant.status)}>{tenant.status}</Badge>
                    </td>
                    <td className="px-2 py-2.5 text-right tabular-nums">
                      {tenant.assigned_numbers}
                    </td>
                    <td className="px-2 py-2.5 text-right tabular-nums">
                      {tenant.calls_today}
                    </td>
                    <td className="px-2 py-2.5 text-right tabular-nums">
                      {tenant.calls_this_month}
                    </td>
                    <td className="px-2 py-2.5 text-right tabular-nums">
                      {tenant.failed_calls_this_month > 0 ? (
                        <span className="font-medium text-destructive">
                          {tenant.failed_calls_this_month}
                        </span>
                      ) : (
                        0
                      )}
                    </td>
                    <td className="px-2 py-2.5">
                      <Badge
                        variant={
                          tenant.calendar_health === "connected" ? "success" : "warning"
                        }
                      >
                        {tenant.calendar_health.replaceAll("_", " ")}
                      </Badge>
                    </td>
                    <td className="px-2 py-2.5 text-muted-foreground">
                      {formatDate(tenant.last_successful_call_at)}
                    </td>
                    <td className="px-2 py-2.5 text-right tabular-nums">
                      {tenant.usage_minutes_this_month}
                    </td>
                    <td className="px-4 py-2.5">
                      {tenant.configuration_ready ? (
                        <Badge variant="success">ready</Badge>
                      ) : (
                        <Badge variant="warning">incomplete</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              {result.data.total} tenant{result.data.total === 1 ? "" : "s"}
            </span>
            <div className="flex gap-2">
              {page > 1 && (
                <Link
                  href={{ query: { ...params, page: page - 1 } }}
                  className="rounded-md border border-border bg-card px-3 py-1.5 hover:bg-muted"
                >
                  Previous
                </Link>
              )}
              {page * result.data.page_size < result.data.total && (
                <Link
                  href={{ query: { ...params, page: page + 1 } }}
                  className="rounded-md border border-border bg-card px-3 py-1.5 hover:bg-muted"
                >
                  Next
                </Link>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
