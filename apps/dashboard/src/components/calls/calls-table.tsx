import Link from "next/link";
import type { CallListItem } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

function formatWhen(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function outcomeVariant(outcome: string | null) {
  switch (outcome) {
    case "booked":
      return "success" as const;
    case "message_taken":
    case "answered_inquiry":
      return "info" as const;
    case "transferred":
      return "warning" as const;
    case "failed":
      return "danger" as const;
    default:
      return "neutral" as const;
  }
}

export function urgencyVariant(urgency: string | null) {
  switch (urgency) {
    case "emergency":
      return "danger" as const;
    case "urgent":
      return "warning" as const;
    default:
      return "neutral" as const;
  }
}

/** Shared calls table; `detailBasePath` decides whether rows link into
 *  the client dashboard or the admin console. */
export function CallsTable({
  items,
  detailBasePath,
}: {
  items: CallListItem[];
  detailBasePath: string;
}) {
  return (
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[960px] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th className="px-4 py-2.5 font-medium">When</th>
            <th className="px-2 py-2.5 font-medium">Caller</th>
            <th className="px-2 py-2.5 text-right font-medium">Duration</th>
            <th className="px-2 py-2.5 font-medium">Outcome</th>
            <th className="px-2 py-2.5 font-medium">Service</th>
            <th className="px-2 py-2.5 font-medium">Urgency</th>
            <th className="px-2 py-2.5 font-medium">Booking</th>
            <th className="px-2 py-2.5 font-medium">Transfer</th>
            <th className="px-2 py-2.5 font-medium">Recording</th>
            <th className="px-4 py-2.5 font-medium">Processing</th>
          </tr>
        </thead>
        <tbody>
          {items.map((call) => (
            <tr
              key={call.id}
              className="border-b border-border last:border-0 hover:bg-muted/50"
            >
              <td className="px-4 py-2.5 whitespace-nowrap">
                <Link
                  href={`${detailBasePath}/${call.id}`}
                  className="font-medium text-primary hover:underline"
                >
                  {formatWhen(call.started_at)}
                </Link>
                {call.transport === "browser_text" && (
                  <span className="ml-2 text-xs text-muted-foreground">test</span>
                )}
              </td>
              <td className="px-2 py-2.5 tabular-nums">
                {call.from_number_last_four ? `···${call.from_number_last_four}` : "—"}
              </td>
              <td className="px-2 py-2.5 text-right tabular-nums">
                {formatDuration(call.duration_seconds)}
              </td>
              <td className="px-2 py-2.5">
                {call.outcome ? (
                  <Badge variant={outcomeVariant(call.outcome)}>
                    {call.outcome.replaceAll("_", " ")}
                  </Badge>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="px-2 py-2.5">{call.service ?? "—"}</td>
              <td className="px-2 py-2.5">
                {call.urgency ? (
                  <Badge variant={urgencyVariant(call.urgency)}>{call.urgency}</Badge>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="px-2 py-2.5">
                {call.booking_status ? (
                  <Badge
                    variant={call.booking_status === "confirmed" ? "success" : "warning"}
                  >
                    {call.booking_status.replaceAll("_", " ")}
                  </Badge>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="px-2 py-2.5">
                {call.transfer_status ? (
                  <Badge variant="warning">{call.transfer_status.replaceAll("_", " ")}</Badge>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="px-2 py-2.5">
                {call.recording_available ? (
                  <Badge variant="info">available</Badge>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="px-4 py-2.5">
                <Badge
                  variant={
                    call.processing_status === "complete"
                      ? "success"
                      : call.processing_status === "failed"
                        ? "danger"
                        : "neutral"
                  }
                >
                  {call.processing_status.replaceAll("_", " ")}
                </Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
