"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useRef } from "react";
import { Input } from "@/components/ui/input";

const URGENCIES = ["emergency", "urgent", "routine"];

const selectClass =
  "h-10 rounded-md border border-border bg-card px-2 text-sm focus-visible:outline-2 focus-visible:outline-ring";

export function MessagesToolbar({ exportHref }: { exportHref: string }) {
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
        <label htmlFor="message-search" className="sr-only">
          Search messages
        </label>
        <Input
          id="message-search"
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
      <label htmlFor="message-urgency" className="sr-only">
        Filter by urgency
      </label>
      <select
        id="message-urgency"
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
      <label htmlFor="message-reviewed" className="sr-only">
        Filter by reviewed status
      </label>
      <select
        id="message-reviewed"
        defaultValue={params.get("reviewed") ?? ""}
        onChange={(event) => update("reviewed", event.target.value)}
        className={selectClass}
      >
        <option value="">All messages</option>
        <option value="no">Needs review</option>
        <option value="yes">Reviewed</option>
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
