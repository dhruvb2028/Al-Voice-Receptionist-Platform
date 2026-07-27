"use server";

import { revalidatePath } from "next/cache";
import { apiFetch } from "@/lib/api";

export interface ActionResult {
  ok: boolean;
  message: string;
  details?: Array<{ field?: string; issue: string }>;
}

async function callConfig(
  tenantId: string,
  path: string,
  init: RequestInit,
): Promise<ActionResult> {
  const result = await apiFetch<unknown>(
    `/admin/tenants/${tenantId}/configuration${path}`,
    init,
  );
  revalidatePath(`/admin/tenants/${tenantId}/configuration`);
  if (!result.ok) {
    // Field-level details come back through the standard error envelope;
    // re-fetch them for validation failures.
    return { ok: false, message: result.message };
  }
  return { ok: true, message: "Done." };
}

export async function saveDraftAction(
  tenantId: string,
  payload: unknown,
): Promise<ActionResult> {
  const result = await apiFetch<unknown>(`/admin/tenants/${tenantId}/configuration/draft`, {
    method: "PUT",
    body: JSON.stringify({ payload }),
  });
  revalidatePath(`/admin/tenants/${tenantId}/configuration`);
  if (!result.ok) {
    return { ok: false, message: result.message, details: result.details };
  }
  return { ok: true, message: "Draft saved." };
}

export async function submitDraftAction(tenantId: string): Promise<ActionResult> {
  const result = await callConfig(tenantId, "/draft/submit", { method: "POST" });
  return result.ok ? { ok: true, message: "Submitted for review." } : result;
}

export async function approveAction(tenantId: string): Promise<ActionResult> {
  const result = await callConfig(tenantId, "/approve", {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
  return result.ok ? { ok: true, message: "Configuration approved and live." } : result;
}

export async function rejectAction(
  tenantId: string,
  notes: string,
): Promise<ActionResult> {
  const result = await callConfig(tenantId, "/reject", {
    method: "POST",
    body: JSON.stringify({ notes }),
  });
  return result.ok ? { ok: true, message: "Draft rejected." } : result;
}

export async function rollbackAction(
  tenantId: string,
  version: number,
): Promise<ActionResult> {
  const result = await callConfig(tenantId, "/rollback", {
    method: "POST",
    body: JSON.stringify({ confirm: true, version }),
  });
  return result.ok ? { ok: true, message: `Rolled back to version ${version}.` } : result;
}
