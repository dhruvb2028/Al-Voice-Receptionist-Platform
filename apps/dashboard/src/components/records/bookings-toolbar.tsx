"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useRef } from "react";
import { Input } from "@/components/ui/input";

const STATUSES = [
  "pending",
  "confirmed",
  "failed",
  "cancelled",
  "reconciliation_required",
];

const selectClass =
  "h-10 rounded-md border border-border bg-card px-2 text-sm focus-visible:outline-2 focus-visible:outline-ring";

export function BookingsToolbar({ exportHref }: { exportHref: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const searchTimer = useRef<number | undefined>(undefined);

  const update = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params);
      if (value) next.set(key, value);
      else next.delete(key);
      next.delete("page");
      router.replace(`${pathname}?${next.toString()}`);
    },
    [router, pathname, params],
  );

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="w-full max-w-56">
        <label htmlFor="booking-search" className="sr-only">
          Search bookings
        </label>
        <Input
          id="booking-search"
          type="search"
          placeholder="Customer name or digits…"
          defaultValue={params.get("search") ?? ""}
          onChange={(event) => {
            const value = event.target.value;
            window.clearTimeout(searchTimer.current);
            searchTimer.current = window.setTimeout(() => update("search", value), 300);
          }}
        />
      </div>
      <label htmlFor="booking-from" className="sr-only">
        Scheduled from
      </label>
      <input
        id="booking-from"
        type="date"
        aria-label="Scheduled from"
        defaultValue={params.get("date_from")?.slice(0, 10) ?? ""}
        onChange={(event) => update("date_from", event.target.value)}
        className={selectClass}
      />
      <label htmlFor="booking-to" className="sr-only">
        Scheduled to
      </label>
      <input
        id="booking-to"
        type="date"
        aria-label="Scheduled to"
        defaultValue={params.get("date_to")?.slice(0, 10) ?? ""}
        onChange={(event) =>
          update("date_to", event.target.value ? `${event.target.value}T23:59:59` : "")
        }
        className={selectClass}
      />
      <label htmlFor="booking-status" className="sr-only">
        Filter by status
      </label>
      <select
        id="booking-status"
        defaultValue={params.get("status") ?? ""}
        onChange={(event) => update("status", event.target.value)}
        className={selectClass}
      >
        <option value="">All statuses</option>
        {STATUSES.map((status) => (
          <option key={status} value={status}>
            {status.replaceAll("_", " ")}
          </option>
        ))}
      </select>
      <label htmlFor="booking-sort" className="sr-only">
        Sort
      </label>
      <select
        id="booking-sort"
        defaultValue={params.get("sort") ?? "-scheduled_at"}
        onChange={(event) => update("sort", event.target.value)}
        className={selectClass}
      >
        <option value="-scheduled_at">Sort: latest first</option>
        <option value="scheduled_at">Sort: earliest first</option>
      </select>
      <a
        href={`${exportHref}?${params.toString()}`}
        className="ml-auto rounded-md border border-border bg-card px-3 py-2 text-sm font-medium hover:bg-muted"
        download
      >
        Export CSV
      </a>
    </div>
  );
}
