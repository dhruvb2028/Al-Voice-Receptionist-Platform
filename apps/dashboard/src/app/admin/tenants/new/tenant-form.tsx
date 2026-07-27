"use client";

import { useActionState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createTenantAction, type CreateTenantState } from "./actions";

const INITIAL: CreateTenantState = { fieldErrors: {}, formError: null };

interface FieldProps {
  label: string;
  name: string;
  error?: string;
  hint?: string;
  children: React.ReactNode;
}

function Field({ label, name, error, hint, children }: FieldProps) {
  return (
    <div className="space-y-1">
      <label htmlFor={name} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {hint && !error && <p className="text-xs text-muted-foreground">{hint}</p>}
      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function NewTenantForm() {
  const [state, formAction, pending] = useActionState(createTenantAction, INITIAL);
  const errors = state.fieldErrors;

  return (
    <form action={formAction} className="space-y-4" noValidate>
      {state.formError && (
        <div
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {state.formError}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Business name" name="business_name" error={errors.business_name}>
          <Input
            id="business_name"
            name="business_name"
            autoComplete="organization"
            required
          />
        </Field>
        <Field
          label="Slug"
          name="slug"
          error={errors.slug}
          hint="Used in URLs — lowercase, hyphens"
        >
          <Input id="slug" name="slug" placeholder="harbor-plumbing" required />
        </Field>
        <Field label="Vertical" name="vertical" error={errors.vertical}>
          <select
            id="vertical"
            name="vertical"
            defaultValue="plumbing"
            className="h-10 w-full rounded-md border border-border bg-card px-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"
          >
            <option value="plumbing">Plumbing</option>
            <option value="hvac">HVAC</option>
            <option value="electrical">Electrical</option>
          </select>
        </Field>
        <Field
          label="Timezone"
          name="timezone"
          error={errors.timezone}
          hint="IANA name, e.g. America/New_York"
        >
          <Input id="timezone" name="timezone" placeholder="America/New_York" required />
        </Field>
        <Field
          label="Owner email"
          name="primary_owner_email"
          error={errors.primary_owner_email}
          hint="Receives the dashboard invitation"
        >
          <Input
            id="primary_owner_email"
            name="primary_owner_email"
            type="email"
            autoComplete="email"
            required
          />
        </Field>
        <Field
          label="Business phone"
          name="primary_phone"
          error={errors.primary_phone}
          hint="+15551234567"
        >
          <Input id="primary_phone" name="primary_phone" type="tel" required />
        </Field>
        <Field
          label="Escalation number"
          name="escalation_number"
          error={errors.escalation_number}
          hint="Where human transfers ring"
        >
          <Input id="escalation_number" name="escalation_number" type="tel" required />
        </Field>
        <Field label="Country" name="country" error={errors.country}>
          <Input id="country" name="country" defaultValue="US" maxLength={2} required />
        </Field>
        <Field
          label="Expected calls / month"
          name="expected_monthly_calls"
          error={errors.expected_monthly_calls}
          hint="Optional — sizing input"
        >
          <Input
            id="expected_monthly_calls"
            name="expected_monthly_calls"
            type="number"
            min={1}
            max={100000}
          />
        </Field>
      </div>

      <div className="flex justify-end gap-2 border-t border-border pt-4">
        <Button type="submit" disabled={pending}>
          {pending ? "Creating…" : "Create tenant"}
        </Button>
      </div>
    </form>
  );
}
