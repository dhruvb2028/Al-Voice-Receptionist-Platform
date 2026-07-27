"use server";

import { redirect } from "next/navigation";
import { z } from "zod";
import { apiFetch } from "@/lib/api";

export const tenantCreateSchema = z.object({
  business_name: z.string().min(2, "Business name is required").max(200),
  slug: z
    .string()
    .regex(/^[a-z0-9][a-z0-9-]{1,78}$/, "Lowercase letters, numbers, and hyphens"),
  timezone: z.string().min(1, "Timezone is required"),
  vertical: z.enum(["plumbing", "hvac", "electrical"]),
  primary_owner_email: z.string().email("A valid owner email is required"),
  primary_phone: z.string().regex(/^\+[1-9][0-9]{6,14}$/, "E.164 format, e.g. +15551234567"),
  escalation_number: z
    .string()
    .regex(/^\+[1-9][0-9]{6,14}$/, "E.164 format, e.g. +15551234567"),
  country: z.string().regex(/^[A-Z]{2}$/, "Two-letter country code"),
  expected_monthly_calls: z.coerce.number().int().min(1).max(100000).optional(),
});

export type TenantCreateInput = z.infer<typeof tenantCreateSchema>;

export interface CreateTenantState {
  fieldErrors: Partial<Record<keyof TenantCreateInput, string>>;
  formError: string | null;
}

export async function createTenantAction(
  _previous: CreateTenantState,
  formData: FormData,
): Promise<CreateTenantState> {
  const raw = Object.fromEntries(formData.entries());
  if (raw.expected_monthly_calls === "") delete raw.expected_monthly_calls;

  const parsed = tenantCreateSchema.safeParse(raw);
  if (!parsed.success) {
    const fieldErrors: CreateTenantState["fieldErrors"] = {};
    for (const issue of parsed.error.issues) {
      const key = issue.path[0] as keyof TenantCreateInput;
      fieldErrors[key] ??= issue.message;
    }
    return { fieldErrors, formError: null };
  }

  const result = await apiFetch<{ id: string }>("/admin/tenants", {
    method: "POST",
    body: JSON.stringify(parsed.data),
  });

  if (!result.ok) {
    return {
      fieldErrors: {},
      formError:
        result.code === "conflict"
          ? "That slug is already in use — choose another."
          : result.message,
    };
  }

  redirect(`/admin/tenants/${result.data.id}`);
}
