import { apiFetch } from "@/lib/api";
import { ErrorState } from "@/components/ui/states";
import { ConfigWorkbench, type ConfigVersionSummary } from "./config-workbench";

export const metadata = { title: "Configuration" };

interface ConfigVersionDetail extends ConfigVersionSummary {
  payload: Record<string, unknown>;
}

export default async function ConfigurationPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  const base = `/admin/tenants/${tenantId}/configuration`;
  const [draft, active, versions] = await Promise.all([
    apiFetch<ConfigVersionDetail | null>(`${base}/draft`),
    apiFetch<ConfigVersionDetail | null>(`${base}/active`),
    apiFetch<ConfigVersionSummary[]>(`${base}/versions`),
  ]);

  if (!draft.ok || !active.ok || !versions.ok) {
    return (
      <ErrorState
        description="Configuration could not be loaded."
        retryHref={`/admin/tenants/${tenantId}/configuration`}
      />
    );
  }

  return (
    <ConfigWorkbench
      tenantId={tenantId}
      draft={draft.data}
      active={active.data}
      versions={versions.data}
    />
  );
}
