"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import { Input } from "@/components/ui/input";

/** Search / filter / sort controls, synced to URL params so the server
 *  component re-renders with the new query. */
export function TenantListToolbar({ statuses }: { statuses: string[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

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
      <div className="w-full max-w-xs">
        <label htmlFor="tenant-search" className="sr-only">
          Search tenants
        </label>
        <Input
          id="tenant-search"
          type="search"
          placeholder="Search by name or slug…"
          defaultValue={params.get("search") ?? ""}
          onChange={(event) => {
            const value = event.target.value;
            // Light debounce via rAF batching; server round-trip is cheap here.
            window.clearTimeout(
              (window as unknown as { __tenantSearchTimer?: number }).__tenantSearchTimer,
            );
            (window as unknown as { __tenantSearchTimer?: number }).__tenantSearchTimer =
              window.setTimeout(() => update("search", value), 300);
          }}
        />
      </div>
      <label htmlFor="tenant-status" className="sr-only">
        Filter by status
      </label>
      <select
        id="tenant-status"
        defaultValue={params.get("status") ?? ""}
        onChange={(event) => update("status", event.target.value)}
        className="h-10 rounded-md border border-border bg-card px-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"
      >
        <option value="">All statuses</option>
        {statuses.map((status) => (
          <option key={status} value={status}>
            {status}
          </option>
        ))}
      </select>
      <label htmlFor="tenant-sort" className="sr-only">
        Sort
      </label>
      <select
        id="tenant-sort"
        defaultValue={params.get("sort") ?? "name"}
        onChange={(event) => update("sort", event.target.value)}
        className="h-10 rounded-md border border-border bg-card px-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"
      >
        <option value="name">Sort: name</option>
        <option value="status">Sort: status</option>
        <option value="created">Sort: newest</option>
      </select>
    </div>
  );
}
