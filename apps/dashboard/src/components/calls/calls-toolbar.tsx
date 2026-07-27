"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useRef } from "react";
import { Input } from "@/components/ui/input";

const OUTCOMES = [
  "booked",
  "message_taken",
  "transferred",
  "answered_inquiry",
  "caller_hangup",
  "failed",
];
const URGENCIES = ["emergency", "urgent", "routine"];
const BOOKING_FILTERS = [
  { value: "confirmed", label: "Booked" },
  { value: "pending", label: "Booking attempted" },
  { value: "none", label: "No booking" },
];

const selectClass =
  "h-10 rounded-md border border-border bg-card px-2 text-sm focus-visible:outline-2 focus-visible:outline-ring";

/** Search / filter / sort controls for the calls list, synced to URL
 *  params so the server component re-renders with the new query. */
export function CallsToolbar({ exportHref }: { exportHref?: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const searchTimer = useRef<number | undefined>(undefined);

  const update = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params);
      if (value) {
        next.set(key, value);
      } else {
        next.delete(key);
      }
      next.delete("page"); // filters reset pagination
      router.replace(`${pathname}?${next.toString()}`);
    },
    [router, pathname, params],
  );

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="w-full max-w-56">
        <label htmlFor="call-search" className="sr-only">
          Search calls
        </label>
        <Input
          id="call-search"
          type="search"
          placeholder="Caller digits or name…"
          defaultValue={params.get("search") ?? ""}
          onChange={(event) => {
            const value = event.target.value;
            window.clearTimeout(searchTimer.current);
            searchTimer.current = window.setTimeout(() => update("search", value), 300);
          }}
        />
      </div>
      <label htmlFor="call-from" className="sr-only">
        From date
      </label>
      <input
        id="call-from"
        type="date"
        defaultValue={params.get("date_from")?.slice(0, 10) ?? ""}
        onChange={(event) => update("date_from", event.target.value)}
        className={selectClass}
        aria-label="From date"
      />
      <label htmlFor="call-to" className="sr-only">
        To date
      </label>
      <input
        id="call-to"
        type="date"
        defaultValue={params.get("date_to")?.slice(0, 10) ?? ""}
        onChange={(event) =>
          update("date_to", event.target.value ? `${event.target.value}T23:59:59` : "")
        }
        className={selectClass}
        aria-label="To date"
      />
      <label htmlFor="call-outcome" className="sr-only">
        Filter by outcome
      </label>
      <select
        id="call-outcome"
        defaultValue={params.get("outcome") ?? ""}
        onChange={(event) => update("outcome", event.target.value)}
        className={selectClass}
      >
        <option value="">All outcomes</option>
        {OUTCOMES.map((outcome) => (
          <option key={outcome} value={outcome}>
            {outcome.replaceAll("_", " ")}
          </option>
        ))}
      </select>
      <label htmlFor="call-urgency" className="sr-only">
        Filter by urgency
      </label>
      <select
        id="call-urgency"
        defaultValue={params.get("urgency") ?? ""}
        onChange={(event) => update("urgency", event.target.value)}
        className={selectClass}
      >
        <option value="">All urgency</option>
        {URGENCIES.map((urgency) => (
          <option key={urgency} value={urgency}>
            {urgency}
          </option>
        ))}
      </select>
      <label htmlFor="call-booking" className="sr-only">
        Filter by booking
      </label>
      <select
        id="call-booking"
        defaultValue={params.get("booking") ?? ""}
        onChange={(event) => update("booking", event.target.value)}
        className={selectClass}
      >
        <option value="">All bookings</option>
        {BOOKING_FILTERS.map((filter) => (
          <option key={filter.value} value={filter.value}>
            {filter.label}
          </option>
        ))}
      </select>
      <label htmlFor="call-sort" className="sr-only">
        Sort
      </label>
      <select
        id="call-sort"
        defaultValue={params.get("sort") ?? "-started_at"}
        onChange={(event) => update("sort", event.target.value)}
        className={selectClass}
      >
        <option value="-started_at">Sort: newest</option>
        <option value="started_at">Sort: oldest</option>
        <option value="-duration">Sort: longest</option>
        <option value="duration">Sort: shortest</option>
      </select>
      {exportHref && (
        <a
          href={`${exportHref}?${params.toString()}`}
          className="ml-auto rounded-md border border-border bg-card px-3 py-2 text-sm font-medium hover:bg-muted"
          download
        >
          Export CSV
        </a>
      )}
    </div>
  );
}
