import Link from "next/link";
import type { ReactNode } from "react";

export const metadata = { title: "Platform Admin" };

/**
 * Admin console shell. Authorization is enforced by the API on every
 * request (admin endpoints 404 for non-admin users); this layout is
 * presentation only.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-56 shrink-0 border-r border-border bg-card md:block">
        <div className="px-4 py-5">
          <Link href="/admin/tenants" className="text-sm font-semibold tracking-tight">
            AI Receptionist
          </Link>
          <p className="mt-0.5 text-xs text-muted-foreground">Platform admin</p>
        </div>
        <nav aria-label="Admin navigation" className="space-y-0.5 px-2">
          <Link
            href="/admin/overview"
            className="block rounded-md px-2 py-1.5 text-sm font-medium text-foreground transition-colors duration-150 hover:bg-muted"
          >
            Overview
          </Link>
          <Link
            href="/admin/tenants"
            className="block rounded-md px-2 py-1.5 text-sm font-medium text-foreground transition-colors duration-150 hover:bg-muted"
          >
            Tenants
          </Link>
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-border bg-card px-4 md:px-6">
          <span className="text-sm font-medium md:hidden">AI Receptionist admin</span>
          <span className="hidden text-sm text-muted-foreground md:block">
            Managed operations console
          </span>
        </header>
        <main className="flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
