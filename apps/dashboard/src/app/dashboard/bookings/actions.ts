"use server";

import { revalidatePath } from "next/cache";
import { apiFetch } from "@/lib/api";

export interface ActionResult {
  ok: boolean;
  message: string;
}

/** Cancellation always sends explicit confirmation; the API rejects the
 *  request without it, and the booking row is never deleted. */
export async function cancelBookingAction(bookingId: string): Promise<ActionResult> {
  const result = await apiFetch<{ status: string; reconciliation_status: string }>(
    `/tenant/bookings/${bookingId}/cancel`,
    { method: "POST", body: JSON.stringify({ confirm: true }) },
  );
  revalidatePath("/dashboard/bookings");
  if (!result.ok) return { ok: false, message: result.message };
  return {
    ok: true,
    message:
      result.data.reconciliation_status === "pending"
        ? "Booking cancelled. The calendar event is being removed."
        : "Booking cancelled.",
  };
}
