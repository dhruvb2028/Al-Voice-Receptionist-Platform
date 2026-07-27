import Link from "next/link";
import { apiFetch, type OnboardingState, type OnboardingStep } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/states";
import { StepControls } from "./step-controls";

export const metadata = { title: "Onboarding" };

const STATUS_VARIANT = {
  complete: "success",
  blocked: "danger",
  pending: "warning",
} as const;

function StepRow({ tenantId, step }: { tenantId: string; step: OnboardingStep }) {
  return (
    <li className="rounded-md border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Badge variant={STATUS_VARIANT[step.status]}>{step.status}</Badge>
            <span className="font-medium">{step.title}</span>
            {!step.attested && (
              <span className="text-xs text-muted-foreground">derived</span>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{step.description}</p>
          {step.detail && <p className="mt-1 text-sm">{step.detail}</p>}
          {step.attested_by && (
            <p className="mt-1 text-xs text-muted-foreground">
              Signed off by {step.attested_by}
              {step.attested_at
                ? ` on ${new Date(step.attested_at).toLocaleDateString()}`
                : ""}
            </p>
          )}
          {step.waived && step.waiver_reason && (
            <p className="mt-1 text-xs text-amber-700">Waived: {step.waiver_reason}</p>
          )}
        </div>
      </div>
      <StepControls tenantId={tenantId} step={step} />
    </li>
  );
}

export default async function OnboardingPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  const result = await apiFetch<OnboardingState>(
    `/admin/tenants/${tenantId}/onboarding`,
  );

  if (!result.ok) {
    return (
      <ErrorState
        description={result.message}
        retryHref={`/admin/tenants/${tenantId}/onboarding`}
      />
    );
  }

  const state = result.data;
  const reports = [
    { slug: "handover", label: "Client handover checklist" },
    { slug: "test-calls", label: "Test call report" },
    { slug: "activation", label: "Activation report" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Onboarding</h2>
          <p className="text-sm text-muted-foreground">
            {state.completed_steps} of {state.total_steps} steps complete ·{" "}
            {state.tenant_status}
          </p>
        </div>
        <Badge variant={state.readiness.ready ? "success" : "warning"}>
          {state.readiness.ready ? "Ready to activate" : "Not ready"}
        </Badge>
      </div>

      {state.readiness.blockers.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Blocking activation</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5 text-sm">
              {state.readiness.blockers.map((blocker) => (
                <li key={blocker.code} className="flex items-start gap-2">
                  <Badge variant={blocker.waivable ? "warning" : "danger"}>
                    {blocker.waivable ? "waivable" : "required"}
                  </Badge>
                  <span>{blocker.message}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Steps</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            {state.steps.map((step) => (
              <StepRow key={step.key} tenantId={tenantId} step={step} />
            ))}
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Reports</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-1.5 text-sm">
            {reports.map((report) => (
              <li key={report.slug}>
                <Link
                  href={`/admin/tenants/${tenantId}/onboarding/reports/${report.slug}`}
                  className="text-primary hover:underline"
                >
                  {report.label}
                </Link>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
