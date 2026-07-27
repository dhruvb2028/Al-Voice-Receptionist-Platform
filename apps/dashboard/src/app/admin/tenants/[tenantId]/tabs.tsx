"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const TABS = [
  { segment: "", label: "Overview" },
  { segment: "configuration", label: "Configuration" },
  { segment: "integrations", label: "Integrations" },
  { segment: "testing", label: "Testing" },
  { segment: "activation", label: "Activation" },
];

export function TenantTabs({ tenantId }: { tenantId: string }) {
  const pathname = usePathname();
  const base = `/admin/tenants/${tenantId}`;

  return (
    <nav
      aria-label="Tenant sections"
      className="flex gap-1 overflow-x-auto border-b border-border"
    >
      {TABS.map((tab) => {
        const href = tab.segment ? `${base}/${tab.segment}` : base;
        const active = pathname === href;
        return (
          <Link
            key={tab.label}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-150",
              active
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
