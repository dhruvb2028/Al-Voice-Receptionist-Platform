import Link from "next/link";
import type { ReactNode } from "react";
import { apiFetch, type AdminTenantView } from "@/lib/api";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { TenantTabs } from "./tabs";

export default async function TenantDetailLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  const result = await apiFetch<AdminTenantView>(`/admin/tenants/${tenantId}`);

  if (!result.ok) {
    return (
      <Card className="mx-auto max-w-3xl p-8 text-center" role="alert">
        <p className="text-sm font-medium">
          {result.status === 404 ? "Tenant not found." : "Could not load this tenant."}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">{result.message}</p>
        <Link
          href="/admin/tenants"
          className="mt-3 inline-block text-sm text-primary hover:underline"
        >
          Back to tenants
        </Link>
      </Card>
    );
  }

  const tenant = result.data;
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div>
        <Link
          href="/admin/tenants"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Tenants
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">{tenant.name}</h1>
          <Badge variant={statusVariant(tenant.status)}>{tenant.status}</Badge>
          <span className="text-sm text-muted-foreground">
            {tenant.vertical} · {tenant.timezone}
          </span>
        </div>
      </div>
      <TenantTabs tenantId={tenant.id} />
      {children}
    </div>
  );
}
