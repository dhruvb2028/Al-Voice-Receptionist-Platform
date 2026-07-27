"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useRef } from "react";
import { Input } from "@/components/ui/input";

export interface FilterOption {
  value: string;
  label: string;
}

export interface FilterConfig {
  key: string;
  label: string;
  options: FilterOption[];
}

interface FilterBarProps {
  searchPlaceholder?: string;
  filters?: FilterConfig[];
}

/** URL-synced search + select filters; server components re-render with
 *  the new query. Changing a filter resets pagination. */
export function FilterBar({ searchPlaceholder = "Search…", filters = [] }: FilterBarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const timer = useRef<number | undefined>(undefined);

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
      <div className="w-full max-w-xs">
        <label htmlFor="filter-search" className="sr-only">
          Search
        </label>
        <Input
          id="filter-search"
          type="search"
          placeholder={searchPlaceholder}
          defaultValue={params.get("search") ?? ""}
          onChange={(event) => {
            const value = event.target.value;
            window.clearTimeout(timer.current);
            timer.current = window.setTimeout(() => update("search", value), 300);
          }}
        />
      </div>
      {filters.map((filter) => (
        <div key={filter.key}>
          <label htmlFor={`filter-${filter.key}`} className="sr-only">
            {filter.label}
          </label>
          <select
            id={`filter-${filter.key}`}
            defaultValue={params.get(filter.key) ?? ""}
            onChange={(event) => update(filter.key, event.target.value)}
            className="h-10 rounded-md border border-border bg-card px-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"
          >
            <option value="">{filter.label}: all</option>
            {filter.options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      ))}
    </div>
  );
}
