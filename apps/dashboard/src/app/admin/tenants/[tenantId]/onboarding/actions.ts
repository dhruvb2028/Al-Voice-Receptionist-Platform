"use server";

import { revalidatePath } from "next/cache";
import { apiFetch, type OnboardingState } from "@/lib/api";

export interface StepResult {
  ok: boolean;
  message: string;
}

export async function recordStepAction(
  tenantId: string,
  stepKey: string,
  passed: boolean,
  note?: string,
): Promise<StepResult> {
  const result = await apiFetch<OnboardingState>(
    `/admin/tenants/${tenantId}/onboarding/${stepKey}/record`,
    { method: "POST", body: JSON.stringify({ passed, note: note || null }) },
  );
  revalidatePath(`/admin/tenants/${tenantId}/onboarding`);
  if (!result.ok) return { ok: false, message: result.message };
  return { ok: true, message: passed ? "Step signed off." : "Sign-off removed." };
}

/** A waiver always carries a justification — the API rejects it otherwise. */
export async function waiveStepAction(
  tenantId: string,
  stepKey: string,
  reason: string,
): Promise<StepResult> {
  const result = await apiFetch<OnboardingState>(
    `/admin/tenants/${tenantId}/onboarding/${stepKey}/waive`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
  revalidatePath(`/admin/tenants/${tenantId}/onboarding`);
  if (!result.ok) return { ok: false, message: result.message };
  return { ok: true, message: "Step waived with a recorded reason." };
}
