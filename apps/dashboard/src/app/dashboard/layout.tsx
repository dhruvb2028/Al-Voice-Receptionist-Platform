import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { apiFetch } from "@/lib/api";
import { PermissionState } from "@/components/ui/states";
import { ToastProvider } from "@/components/ui/toast";
import { DashboardShell } from "./shell";

interface MeResponse {
  external_user_id: string;
  role: string;
  tenant_id: string | null;
}

interface TenantResponse {
  id: string;
  name: string;
  status: string;
  timezone: string;
}

export const metadata = { title: "Dashboard" };

export default async function DashboardLayout({ children }: { children: ReactNode }) {
  const me = await apiFetch<MeResponse>("/me");

  // Unauthenticated → sign-in (middleware normally handles this before we
  // get here; this covers direct API-session mismatches).
  if (!me.ok && me.status === 401) {
    redirect("/sign-in");
  }

  // Authenticated but no valid tenant membership (or suspended tenant).
  if (!me.ok || (me.data.role !== "platform_admin" && !me.data.tenant_id)) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <PermissionState
          title="Your account isn't linked to a business yet"
          description={
            !me.ok && me.status === 403
              ? me.message
              : "If your business uses this platform, ask the owner to invite you. Otherwise contact support."
          }
        />
      </div>
    );
  }

  // Platform admins have their own console; the client dashboard needs a
  // tenant scope.
  if (me.data.role === "platform_admin") {
    redirect("/admin/tenants");
  }

  const tenant = await apiFetch<TenantResponse>("/tenant");
  const environment = process.env.NODE_ENV === "production" ? null : "development";

  return (
    <ToastProvider>
      <DashboardShell
        role={me.data.role}
        tenantName={tenant.ok ? tenant.data.name : "Your business"}
        tenantStatus={tenant.ok ? tenant.data.status : "unknown"}
        planLabel="Managed service"
        environment={environment}
      >
        {children}
      </DashboardShell>
    </ToastProvider>
  );
}
