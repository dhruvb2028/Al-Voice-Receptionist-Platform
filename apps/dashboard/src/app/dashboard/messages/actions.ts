"use server";

import { revalidatePath } from "next/cache";
import { apiFetch, type MessageListItem } from "@/lib/api";

export interface ActionResult {
  ok: boolean;
  message: string;
}

export async function setMessageReviewedAction(
  messageId: string,
  reviewed: boolean,
): Promise<ActionResult> {
  const result = await apiFetch<MessageListItem>(`/tenant/messages/${messageId}/review`, {
    method: "POST",
    body: JSON.stringify({ reviewed }),
  });
  revalidatePath("/dashboard/messages");
  if (!result.ok) return { ok: false, message: result.message };
  return { ok: true, message: reviewed ? "Marked as reviewed." : "Marked as not reviewed." };
}

/** Internal notes stay inside the dashboard — the receptionist never
 *  reads them aloud. */
export async function saveMessageNoteAction(
  messageId: string,
  note: string,
): Promise<ActionResult> {
  const result = await apiFetch<MessageListItem>(`/tenant/messages/${messageId}/note`, {
    method: "PUT",
    body: JSON.stringify({ note }),
  });
  revalidatePath("/dashboard/messages");
  if (!result.ok) return { ok: false, message: result.message };
  return { ok: true, message: note.trim() ? "Note saved." : "Note removed." };
}
