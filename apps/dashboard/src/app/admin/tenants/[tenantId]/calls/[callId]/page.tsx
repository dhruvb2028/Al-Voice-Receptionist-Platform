import Link from "next/link";
import { apiFetch, type CallDetail } from "@/lib/api";
import { CallDetailView } from "@/components/calls/call-detail-view";
import { ErrorState } from "@/components/ui/states";

export const metadata = { title: "Call inspection" };

export default async function AdminCallDetailPage({
  params,
}: {
  params: Promise<{ tenantId: string; callId: string }>;
}) {
  const { tenantId, callId } = await params;
  const result = await apiFetch<CallDetail>(
    `/admin/tenants/${tenantId}/calls/${callId}`,
  );
  const base = `/admin/tenants/${tenantId}/calls`;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link href={base} className="text-sm text-muted-foreground hover:text-foreground">
          ← Calls
        </Link>
        <h2 className="text-lg font-semibold tracking-tight">Call inspection</h2>
      </div>

      {!result.ok ? (
        <ErrorState description={result.message} retryHref={base} />
      ) : (
        <CallDetailView call={result.data} admin />
      )}
    </div>
  );
}
