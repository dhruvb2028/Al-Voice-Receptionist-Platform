"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import type { BookingListItem } from "@/lib/api";
import { cancelBookingAction } from "@/app/dashboard/bookings/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useToast } from "@/components/ui/toast";

function formatSchedule(value: string, timezone: string): string {
  const when = new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return `${when} · ${timezone}`;
}

function bookingVariant(status: string) {
  switch (status) {
    case "confirmed":
      return "success" as const;
    case "pending":
      return "warning" as const;
    case "cancelled":
      return "neutral" as const;
    default:
      return "danger" as const;
  }
}

export function BookingsTable({ items }: { items: BookingListItem[] }) {
  const [pendingCancel, setPendingCancel] = useState<BookingListItem | null>(null);
  const [isPending, startTransition] = useTransition();
  const { toast } = useToast();

  function confirmCancel() {
    const booking = pendingCancel;
    if (!booking) return;
    startTransition(async () => {
      const result = await cancelBookingAction(booking.id);
      setPendingCancel(null);
      toast({
        title: result.ok ? "Booking cancelled" : "Couldn't cancel booking",
        description: result.message,
        tone: result.ok ? "success" : "error",
      });
    });
  }

  return (
    <>
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[1000px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-4 py-2.5 font-medium">Customer</th>
              <th className="px-2 py-2.5 font-medium">Phone</th>
              <th className="px-2 py-2.5 font-medium">Service</th>
              <th className="px-2 py-2.5 font-medium">Scheduled</th>
              <th className="px-2 py-2.5 font-medium">Address</th>
              <th className="px-2 py-2.5 font-medium">Calendar</th>
              <th className="px-2 py-2.5 font-medium">Status</th>
              <th className="px-2 py-2.5 font-medium">Call</th>
              <th className="px-2 py-2.5 font-medium">Created</th>
              <th className="px-4 py-2.5 font-medium">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((booking) => (
              <tr
                key={booking.id}
                className="border-b border-border last:border-0 hover:bg-muted/50"
              >
                <td className="px-4 py-2.5 font-medium">
                  {booking.customer_name ?? "—"}
                </td>
                <td className="px-2 py-2.5 tabular-nums">
                  {booking.phone_last_four ? `···${booking.phone_last_four}` : "—"}
                </td>
                <td className="px-2 py-2.5">{booking.service ?? "—"}</td>
                <td className="px-2 py-2.5 whitespace-nowrap">
                  {formatSchedule(booking.scheduled_at, booking.timezone)}
                </td>
                <td className="px-2 py-2.5 max-w-56 truncate" title={booking.address ?? ""}>
                  {booking.address ?? "—"}
                </td>
                <td className="px-2 py-2.5">
                  {booking.calendar_status === "linked" ? (
                    <Badge variant="info">linked</Badge>
                  ) : (
                    <span className="text-muted-foreground">not linked</span>
                  )}
                </td>
                <td className="px-2 py-2.5">
                  <Badge variant={bookingVariant(booking.status)}>
                    {booking.status.replaceAll("_", " ")}
                  </Badge>
                </td>
                <td className="px-2 py-2.5">
                  {booking.call_id ? (
                    <Link
                      href={`/dashboard/calls/${booking.call_id}`}
                      className="text-primary hover:underline"
                    >
                      View call
                    </Link>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
                <td className="px-2 py-2.5 whitespace-nowrap text-muted-foreground">
                  {new Date(booking.created_at).toLocaleDateString()}
                </td>
                <td className="px-4 py-2.5 text-right">
                  {booking.status !== "cancelled" && (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setPendingCancel(booking)}
                    >
                      Cancel
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <ConfirmDialog
        open={pendingCancel !== null}
        title="Cancel this booking?"
        description={
          pendingCancel ? (
            <>
              <p>
                {pendingCancel.customer_name ?? "This customer"} is booked for{" "}
                {formatSchedule(pendingCancel.scheduled_at, pendingCancel.timezone)}.
              </p>
              <p className="mt-2">
                The booking stays in your history as cancelled
                {pendingCancel.calendar_status === "linked"
                  ? ", and the calendar event will be removed."
                  : "."}{" "}
                Let the customer know yourself — this doesn&apos;t contact them.
              </p>
            </>
          ) : undefined
        }
        confirmLabel="Cancel booking"
        destructive
        pending={isPending}
        onConfirm={confirmCancel}
        onCancel={() => setPendingCancel(null)}
      />
    </>
  );
}
