import Link from "next/link";
import { apiFetch, type CallListPage } from "@/lib/api";
import { CallsTable } from "@/components/calls/calls-table";
import { CallsToolbar } from "@/components/calls/calls-toolbar";
import { EmptyState, ErrorState } from "@/components/ui/states";

export const metadata = { title: "Tenant calls" };

interface CallsSearchParams {
  search?: string;
  date_from?: string;
  date_to?: string;
  outcome?: string;
  urgency?: string;
  booking?: string;
  sort?: string;
  page?: string;
}

export default async function AdminTenantCallsPage({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<CallsSearchParams>;
}) {
  const { tenantId } = await params;
  const query = await searchParams;
  const page = Math.max(1, Number(query.page) || 1);
  const result = await apiFetch<CallListPage>(`/admin/tenants/${tenantId}/calls`, {
    searchParams: { ...query, page: String(page) },
  });
  const base = `/admin/tenants/${tenantId}/calls`;

  return (
    <div className="space-y-4">
      <CallsToolbar />

      {!result.ok ? (
        <ErrorState description={result.message} retryHref={base} />
      ) : result.data.items.length === 0 ? (
        <EmptyState
          title="No calls found"
          description="This tenant has no calls matching the current filters."
        />
      ) : (
        <>
          <CallsTable items={result.data.items} detailBasePath={base} />
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              {result.data.total} call{result.data.total === 1 ? "" : "s"}
            </span>
            <div className="flex gap-2">
              {page > 1 && (
                <Link
                  href={{ query: { ...query, page: page - 1 } }}
                  className="rounded-md border border-border bg-card px-3 py-1.5 hover:bg-muted"
                >
                  Previous
                </Link>
              )}
              {page * result.data.page_size < result.data.total && (
                <Link
                  href={{ query: { ...query, page: page + 1 } }}
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
