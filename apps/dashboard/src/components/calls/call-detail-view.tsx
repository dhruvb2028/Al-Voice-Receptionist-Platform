import type { CallDetail } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Transcript } from "./transcript";
import { outcomeVariant, urgencyVariant } from "./calls-table";

function formatWhen(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}m ${rest}s`;
}

function formatCents(cents: number | null): string {
  if (cents === null) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm">{children}</dd>
    </div>
  );
}

const TIMELINE_DOT: Record<string, string> = {
  call: "bg-slate-400",
  tool: "bg-violet-400",
  guardrail: "bg-amber-400",
  booking: "bg-emerald-400",
  message: "bg-blue-400",
  escalation: "bg-red-400",
};

/** Full call detail; `admin` unlocks the technical expansion and
 *  `recordingUrl` (when present) renders the audio player. */
export function CallDetailView({
  call,
  admin = false,
  recordingUrl,
}: {
  call: CallDetail;
  admin?: boolean;
  recordingUrl?: string | null;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        {call.processing_status === "failed" && (
          <div
            role="status"
            className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
          >
            <p className="font-medium">Call analysis didn&apos;t finish</p>
            <p className="mt-0.5">
              The summary and outcome for this call may be incomplete. The transcript
              and any bookings or messages below are unaffected.
            </p>
          </div>
        )}
        {call.processing_status === "pending" && (
          <div
            role="status"
            className="rounded-md border border-border bg-muted/50 px-4 py-3 text-sm text-muted-foreground"
          >
            This call is still being processed — the summary and outcome will appear
            shortly.
          </div>
        )}
        <Card>
          <CardHeader className="flex items-center justify-between gap-2">
            <CardTitle>Call overview</CardTitle>
            <div className="flex gap-2">
              {call.outcome && (
                <Badge variant={outcomeVariant(call.outcome)}>
                  {call.outcome.replaceAll("_", " ")}
                </Badge>
              )}
              {call.urgency && (
                <Badge variant={urgencyVariant(call.urgency)}>{call.urgency}</Badge>
              )}
            </div>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Fact label="Started">{formatWhen(call.started_at)}</Fact>
              <Fact label="Duration">{formatDuration(call.duration_seconds)}</Fact>
              <Fact label="Caller">
                {call.from_number_last_four ? `···${call.from_number_last_four}` : "—"}
              </Fact>
              <Fact label="Channel">
                {call.transport === "browser_text" ? "Browser test" : "Phone"}
              </Fact>
            </dl>
            {call.summary && (
              <div className="mt-4 rounded-md bg-muted/60 p-3">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Summary
                </p>
                <p className="mt-1 text-sm leading-relaxed">{call.summary}</p>
                <div className="mt-2 flex gap-2 text-xs text-muted-foreground">
                  {call.sentiment && <span>Sentiment: {call.sentiment}</span>}
                  {call.follow_up_required && (
                    <Badge variant="warning">follow-up needed</Badge>
                  )}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {(call.recording_available || recordingUrl) && (
          <Card>
            <CardHeader>
              <CardTitle>Recording</CardTitle>
            </CardHeader>
            <CardContent>
              {recordingUrl ? (
                <audio controls preload="none" src={recordingUrl} className="w-full" />
              ) : (
                <p className="text-sm text-muted-foreground">
                  The recording is stored but temporarily unavailable. Reload the page
                  to try again.
                </p>
              )}
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Transcript</CardTitle>
          </CardHeader>
          <CardContent>
            <Transcript
              turns={call.turns}
              tools={call.tools}
              guardrails={call.guardrails}
              escalation={call.escalation}
              admin={admin}
            />
          </CardContent>
        </Card>

        {admin && (
          <Card>
            <CardHeader>
              <CardTitle>Technical details</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <Fact label="Provider call SID">
                  <code className="text-xs">{call.provider_call_sid ?? "—"}</code>
                </Fact>
                <Fact label="Recording status">{call.recording_status ?? "—"}</Fact>
                <Fact label="Transcript status">{call.transcript_status}</Fact>
                <Fact label="Processing">{call.processing_status}</Fact>
                <Fact label="Failure category">{call.failure_category ?? "—"}</Fact>
                <Fact label="Failure detail">{call.failure_detail_safe ?? "—"}</Fact>
              </dl>
              {call.turns.some((t) => t.total_latency_ms !== null) && (
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th className="py-1.5 pr-2 font-medium">Turn</th>
                        <th className="py-1.5 pr-2 text-right font-medium">Endpointing</th>
                        <th className="py-1.5 pr-2 text-right font-medium">STT</th>
                        <th className="py-1.5 pr-2 text-right font-medium">LLM TTFT</th>
                        <th className="py-1.5 pr-2 text-right font-medium">TTS TTFB</th>
                        <th className="py-1.5 text-right font-medium">Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {call.turns.map((turn) => (
                        <tr key={turn.turn_index} className="border-b border-border last:border-0">
                          <td className="py-1.5 pr-2">
                            {turn.turn_index} · {turn.role}
                          </td>
                          <td className="py-1.5 pr-2 text-right tabular-nums">
                            {turn.endpointing_ms ?? "—"}
                          </td>
                          <td className="py-1.5 pr-2 text-right tabular-nums">
                            {turn.stt_finalization_ms ?? "—"}
                          </td>
                          <td className="py-1.5 pr-2 text-right tabular-nums">
                            {turn.llm_ttft_ms ?? "—"}
                          </td>
                          <td className="py-1.5 pr-2 text-right tabular-nums">
                            {turn.tts_ttfb_ms ?? "—"}
                          </td>
                          <td className="py-1.5 text-right tabular-nums">
                            {turn.total_latency_ms ?? "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>

      <div className="space-y-4">
        {call.booking && (
          <Card>
            <CardHeader>
              <CardTitle>Booking</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex items-center justify-between">
                <span>{call.booking.service ?? "Service visit"}</span>
                <Badge
                  variant={call.booking.status === "confirmed" ? "success" : "warning"}
                >
                  {call.booking.status.replaceAll("_", " ")}
                </Badge>
              </div>
              <p className="text-muted-foreground">
                {formatWhen(call.booking.scheduled_at)} ({call.booking.timezone})
              </p>
              {call.booking.customer_name && <p>{call.booking.customer_name}</p>}
            </CardContent>
          </Card>
        )}

        {call.message && (
          <Card>
            <CardHeader>
              <CardTitle>Message</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex items-center justify-between">
                <span>{call.message.customer_name ?? "Caller"}</span>
                <Badge variant={urgencyVariant(call.message.urgency)}>
                  {call.message.urgency}
                </Badge>
              </div>
              <p className="text-muted-foreground">
                Delivery: {call.message.delivery_status}
              </p>
            </CardContent>
          </Card>
        )}

        {call.escalation && (
          <Card>
            <CardHeader>
              <CardTitle>Escalation</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              <p>Reason: {call.escalation.reason.replaceAll("_", " ")}</p>
              <p>Status: {call.escalation.status.replaceAll("_", " ")}</p>
              <p className="text-muted-foreground">
                {formatWhen(call.escalation.initiated_at)}
              </p>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Timeline</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="space-y-2">
              {call.timeline.map((entry, index) => (
                <li key={index} className="flex items-start gap-2 text-sm">
                  <span
                    className={`mt-1.5 size-2 shrink-0 rounded-full ${
                      TIMELINE_DOT[entry.kind] ?? "bg-slate-400"
                    }`}
                    aria-hidden
                  />
                  <div>
                    <p>{entry.label}</p>
                    <p className="text-xs text-muted-foreground">
                      {formatWhen(entry.at)}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Usage &amp; cost</CardTitle>
          </CardHeader>
          <CardContent>
            {call.usage.length === 0 ? (
              <p className="text-sm text-muted-foreground">No usage recorded.</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {call.usage.map((entry, index) => (
                  <li key={index} className="flex justify-between gap-2">
                    <span className="text-muted-foreground">
                      {entry.usage_type.replaceAll("_", " ")}
                      {admin && entry.provider ? ` (${entry.provider})` : ""}
                    </span>
                    <span className="tabular-nums">
                      {entry.quantity.toLocaleString()} {entry.unit}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-3 flex justify-between border-t border-border pt-2 text-sm font-medium">
              <span>Estimated cost</span>
              <span className="tabular-nums">{formatCents(call.estimated_cost_cents)}</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
