import Link from "next/link";

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  /** Current query params to preserve while changing pages. */
  params?: Record<string, string | undefined>;
}

export function Pagination({ page, pageSize, total, params = {} }: PaginationProps) {
  const hasPrevious = page > 1;
  const hasNext = page * pageSize < total;
  if (!hasPrevious && !hasNext) return null;

  const linkClass =
    "rounded-md border border-border bg-card px-3 py-1.5 text-sm hover:bg-muted";

  return (
    <nav aria-label="Pagination" className="flex items-center justify-between">
      <span className="text-sm text-muted-foreground">
        Page {page} of {Math.max(1, Math.ceil(total / pageSize))}
      </span>
      <div className="flex gap-2">
        {hasPrevious && (
          <Link href={{ query: { ...params, page: page - 1 } }} className={linkClass}>
            Previous
          </Link>
        )}
        {hasNext && (
          <Link href={{ query: { ...params, page: page + 1 } }} className={linkClass}>
            Next
          </Link>
        )}
      </div>
    </nav>
  );
}
