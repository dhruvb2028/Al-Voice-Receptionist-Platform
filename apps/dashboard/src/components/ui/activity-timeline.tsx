import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface TimelineItem {
  id: string;
  title: ReactNode;
  description?: ReactNode;
  timestamp: string;
  tone?: "neutral" | "success" | "warning" | "danger";
}

const DOT_TONES: Record<NonNullable<TimelineItem["tone"]>, string> = {
  neutral: "bg-slate-400",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-red-500",
};

export function ActivityTimeline({ items }: { items: TimelineItem[] }) {
  return (
    <ol className="space-y-0">
      {items.map((item, index) => (
        <li key={item.id} className="relative flex gap-3 pb-5 last:pb-0">
          {index < items.length - 1 && (
            <span
              aria-hidden
              className="absolute left-[5px] top-4 h-full w-px bg-border"
            />
          )}
          <span
            aria-hidden
            className={cn(
              "mt-1.5 size-2.5 shrink-0 rounded-full",
              DOT_TONES[item.tone ?? "neutral"],
            )}
          />
          <div className="min-w-0">
            <p className="text-sm font-medium">{item.title}</p>
            {item.description && (
              <p className="text-sm text-muted-foreground">{item.description}</p>
            )}
            <p className="mt-0.5 text-xs text-muted-foreground">{item.timestamp}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}
