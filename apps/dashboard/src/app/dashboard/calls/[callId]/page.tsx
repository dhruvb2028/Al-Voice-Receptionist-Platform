import Link from "next/link";
import {
  apiFetch,
  type CallDetail,
  type RecordingUrlResponse,
} from "@/lib/api";
import { CallDetailView } from "@/components/calls/call-detail-view";
import { ErrorState } from "@/components/ui/states";

export const metadata = { title: "Call detail" };

export default async function CallDetailPage({
  params,
}: {
  params: Promise<{ callId: string }>;
}) {
  const { callId } = await params;
  const result = await apiFetch<CallDetail>(`/tenant/calls/${callId}`);

  let recordingUrl: string | null = null;
  if (result.ok && result.data.recording_available) {
    const signed = await apiFetch<RecordingUrlResponse>(
      `/tenant/calls/${callId}/recording-url`,
    );
    if (signed.ok) recordingUrl = signed.data.url;
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex items-center gap-3">
        <Link
          href="/dashboard/calls"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Calls
        </Link>
        <h1 className="text-xl font-semibold tracking-tight">Call detail</h1>
      </div>

      {!result.ok ? (
        result.status === 404 ? (
          <ErrorState
            title="Call not found"
            description="This call doesn't exist or belongs to another account."
            retryHref="/dashboard/calls"
          />
        ) : (
          <ErrorState description={result.message} retryHref="/dashboard/calls" />
        )
      ) : (
        <CallDetailView call={result.data} recordingUrl={recordingUrl} />
      )}
    </div>
  );
}
