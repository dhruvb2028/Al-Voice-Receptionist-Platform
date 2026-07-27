"use server";

import { revalidatePath } from "next/cache";
import { apiFetch } from "@/lib/api";

export interface LifecycleResult {
  ok: boolean;
  message: string;
}

async function lifecycle(
  tenantId: string,
  action: "activate" | "pause" | "begin-testing",
): Promise<LifecycleResult> {
  const result = await apiFetch<{ status: string }>(
    `/admin/tenants/${tenantId}/${action}`,
    { method: "POST", body: JSON.stringify({ confirm: true }) },
  );
  revalidatePath(`/admin/tenants/${tenantId}`);
  if (!result.ok) return { ok: false, message: result.message };
  return { ok: true, message: `Tenant is now ${result.data.status}.` };
}

export async function activateTenantAction(tenantId: string): Promise<LifecycleResult> {
  return lifecycle(tenantId, "activate");
}

export async function pauseTenantAction(tenantId: string): Promise<LifecycleResult> {
  return lifecycle(tenantId, "pause");
}

export async function beginTestingAction(tenantId: string): Promise<LifecycleResult> {
  return lifecycle(tenantId, "begin-testing");
}
