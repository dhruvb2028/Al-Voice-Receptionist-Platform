import {
  apiFetch,
  type ActivationReadiness,
  type AdminTenantView,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LifecycleControls } from "./lifecycle-controls";

export const metadata = { title: "Activation" };

export default async function ActivationPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  const [readiness, tenant] = await Promise.all([
    apiFetch<ActivationReadiness>(`/admin/tenants/${tenantId}/activation-readiness`),
    apiFetch<AdminTenantView>(`/admin/tenants/${tenantId}`),
  ]);

  if (!readiness.ok || !tenant.ok) {
    return (
      <Card className="p-8 text-center" role="alert">
        <p className="text-sm font-medium">Could not load activation readiness.</p>
        <p className="mt-1 text-sm text-muted-foreground">
          {!readiness.ok ? readiness.message : !tenant.ok ? tenant.message : ""}
        </p>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Activation checklist</CardTitle>
          <span
            className={
              readiness.data.ready
                ? "text-sm font-medium text-accent"
                : "text-sm font-medium text-warning"
            }
          >
            {readiness.data.ready
              ? "All checks passed"
              : `${readiness.data.blockers.length} blocker${
                  readiness.data.blockers.length === 1 ? "" : "s"
                } remaining`}
          </span>
        </CardHeader>
        <CardContent>
          {readiness.data.ready ? (
            <p className="text-sm text-muted-foreground">
              Every requirement is satisfied. Activation makes this tenant live for real
              callers.
            </p>
          ) : (
            <ul className="space-y-2">
              {readiness.data.blockers.map((blocker) => (
                <li key={blocker.code} className="flex items-start gap-2 text-sm">
                  <span
                    aria-hidden
                    className="mt-1.5 size-2 shrink-0 rounded-full bg-amber-500"
                  />
                  <div>
                    <span>{blocker.message}</span>
                    {blocker.waivable && (
                      <span className="ml-2 text-xs text-muted-foreground">
                        (waivable)
                      </span>
                    )}
                    <code className="ml-2 rounded bg-muted px-1 py-0.5 text-xs text-muted-foreground">
                      {blocker.code}
                    </code>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <LifecycleControls
        tenantId={tenantId}
        status={tenant.data.status}
        ready={readiness.data.ready}
      />
    </div>
  );
}
