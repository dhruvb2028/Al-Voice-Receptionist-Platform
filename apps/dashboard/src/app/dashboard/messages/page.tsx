import Link from "next/link";
import { apiFetch, type MessageListPage } from "@/lib/api";
import { MessageCard } from "@/components/records/message-card";
import { MessagesToolbar } from "@/components/records/messages-toolbar";
import { EmptyState, ErrorState } from "@/components/ui/states";

export const metadata = { title: "Messages" };

interface MessageSearchParams {
  search?: string;
  urgency?: string;
  reviewed?: string;
  page?: string;
}

export default async function MessagesPage({
  searchParams,
}: {
  searchParams: Promise<MessageSearchParams>;
}) {
  const params = await searchParams;
  const page = Math.max(1, Number(params.page) || 1);
  const result = await apiFetch<MessageListPage>("/tenant/messages", {
    searchParams: { ...params, page: String(page) },
  });
  const hasFilters = Boolean(params.search || params.urgency || params.reviewed);

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Messages</h1>
        <p className="text-sm text-muted-foreground">
          Messages your receptionist took, newest first. Notes you add here stay
          internal.
        </p>
      </div>

      <MessagesToolbar exportHref="/dashboard/messages/export" />

      {!result.ok ? (
        <ErrorState description={result.message} retryHref="/dashboard/messages" />
      ) : result.data.items.length === 0 ? (
        <EmptyState
          title={hasFilters ? "No messages match the current filters" : "No messages yet"}
          description={
            hasFilters
              ? "Try clearing the urgency or review filters."
              : "When a caller leaves a message, it appears here with their details and urgency."
          }
        />
      ) : (
        <>
          <div className="space-y-3">
            {result.data.items.map((message) => (
              <MessageCard key={message.id} message={message} />
            ))}
          </div>
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              {result.data.total} message{result.data.total === 1 ? "" : "s"}
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
