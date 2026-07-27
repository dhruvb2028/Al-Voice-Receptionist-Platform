import Link from "next/link";
import { apiFetch, type BookingListPage } from "@/lib/api";
import { BookingsTable } from "@/components/records/bookings-table";
import { BookingsToolbar } from "@/components/records/bookings-toolbar";
import { EmptyState, ErrorState } from "@/components/ui/states";

export const metadata = { title: "Bookings" };

interface BookingSearchParams {
  search?: string;
  status?: string;
  date_from?: string;
  date_to?: string;
  sort?: string;
  page?: string;
}

export default async function BookingsPage({
  searchParams,
}: {
  searchParams: Promise<BookingSearchParams>;
}) {
  const params = await searchParams;
  const page = Math.max(1, Number(params.page) || 1);
  const result = await apiFetch<BookingListPage>("/tenant/bookings", {
    searchParams: { ...params, page: String(page) },
  });
  const hasFilters = Boolean(
    params.search || params.status || params.date_from || params.date_to,
  );

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Bookings</h1>
        <p className="text-sm text-muted-foreground">
          Appointments your receptionist booked, with calendar status.
        </p>
      </div>

      <BookingsToolbar exportHref="/dashboard/bookings/export" />

      {!result.ok ? (
        <ErrorState description={result.message} retryHref="/dashboard/bookings" />
      ) : result.data.items.length === 0 ? (
        <EmptyState
          title={hasFilters ? "No bookings match the current filters" : "No bookings yet"}
          description={
            hasFilters
              ? "Try widening the date range or clearing filters."
              : "Appointments booked during calls will appear here, with the caller's details and calendar status."
          }
        />
      ) : (
        <>
          <BookingsTable items={result.data.items} />
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              {result.data.total} booking{result.data.total === 1 ? "" : "s"}
            </span>
            <div className="flex gap-2">
              {page > 1 && (
                <Link
                  href={{ query: { ...params, page: page - 1 } }}
                  className="rounded-md border border-border bg-card px-3 py-1.5 hover:bg-muted"
                >
                  Previous
                </Link>
              )}
              {page * result.data.page_size < result.data.total && (
                <Link
                  href={{ query: { ...params, page: page + 1 } }}
                  className="rounded-md border border-border bg-card px-3 py-1.5 hover:bg-muted"
                >
                  Next
                </Link>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
