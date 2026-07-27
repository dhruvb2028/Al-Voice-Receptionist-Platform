import { apiFetch, type AdminTenantView } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata = { title: "Tenant overview" };

export default async function TenantOverviewPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  const result = await apiFetch<AdminTenantView>(`/admin/tenants/${tenantId}`);
  if (!result.ok) return null; // layout already rendered the error state

  const tenant = result.data;
  const rows: Array<[string, string]> = [
    ["Slug", tenant.slug],
    ["Vertical", tenant.vertical],
    ["Timezone", tenant.timezone],
    ["Country", tenant.country],
    [
      "Expected calls / month",
      tenant.expected_monthly_calls ? String(tenant.expected_monthly_calls) : "—",
    ],
    ["Auth organization", tenant.external_auth_org_id ?? "not linked"],
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Business record</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                {label}
              </dt>
              <dd className="mt-0.5 text-sm">{value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
