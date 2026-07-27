"use client";

import { UserButton } from "@clerk/nextjs";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  BarChart3,
  Bot,
  Building2,
  Calendar,
  LayoutDashboard,
  Menu,
  MessageSquare,
  Phone,
  PhoneCall,
  Plug,
  Settings,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Badge, statusVariant } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  ownerOnly?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: "/dashboard/calls", label: "Calls", icon: PhoneCall },
  { href: "/dashboard/bookings", label: "Bookings", icon: Calendar },
  { href: "/dashboard/messages", label: "Messages", icon: MessageSquare },
  { href: "/dashboard/receptionist", label: "AI Receptionist", icon: Bot },
  { href: "/dashboard/business", label: "Business Configuration", icon: Building2 },
  { href: "/dashboard/phone-numbers", label: "Phone Numbers", icon: Phone },
  { href: "/dashboard/integrations", label: "Integrations", icon: Plug },
  { href: "/dashboard/usage", label: "Usage", icon: BarChart3, ownerOnly: true },
  { href: "/dashboard/team", label: "Team", icon: Users, ownerOnly: true },
  { href: "/dashboard/settings", label: "Settings", icon: Settings, ownerOnly: true },
];

interface ShellProps {
  role: string;
  tenantName: string;
  tenantStatus: string;
  planLabel: string;
  environment: string | null;
  children: ReactNode;
}

function NavLinks({
  role,
  pathname,
  onNavigate,
}: {
  role: string;
  pathname: string;
  onNavigate?: () => void;
}) {
  const visible = NAV_ITEMS.filter((item) => !item.ownerOnly || role === "client_owner");
  return (
    <ul className="space-y-0.5">
      {visible.map((item) => {
        const active =
          item.href === "/dashboard"
            ? pathname === item.href
            : pathname.startsWith(item.href);
        const Icon = item.icon;
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors duration-150",
                active
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon aria-hidden className="size-4 shrink-0" />
              {item.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export function DashboardShell({
  role,
  tenantName,
  tenantStatus,
  planLabel,
  environment,
  children,
}: ShellProps) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const reduceMotion = useReducedMotion();

  // Focus management: opening the drawer focuses its close button;
  // Escape closes it.
  useEffect(() => {
    if (!mobileOpen) return;
    closeRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setMobileOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileOpen]);

  const tenantHeader = (
    <div className="border-b border-border px-4 py-4">
      <p className="truncate text-sm font-semibold">{tenantName}</p>
      <div className="mt-1 flex items-center gap-2">
        <Badge variant={statusVariant(tenantStatus)}>{tenantStatus}</Badge>
        <span className="text-xs text-muted-foreground">{planLabel}</span>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside className="hidden w-64 shrink-0 flex-col border-r border-border bg-card lg:flex">
        {tenantHeader}
        <nav aria-label="Dashboard navigation" className="flex-1 overflow-y-auto p-2">
          <NavLinks role={role} pathname={pathname} />
        </nav>
      </aside>

      {/* Mobile drawer */}
      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-40 bg-black/40 lg:hidden"
            onClick={() => setMobileOpen(false)}
          >
            <motion.aside
              initial={reduceMotion ? false : { x: -24, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={reduceMotion ? { opacity: 0 } : { x: -24, opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="flex h-full w-72 max-w-[85vw] flex-col border-r border-border bg-card"
              onClick={(event) => event.stopPropagation()}
              aria-label="Dashboard navigation"
            >
              <div className="flex items-center justify-between border-b border-border px-4 py-3">
                <span className="text-sm font-semibold">{tenantName}</span>
                <button
                  ref={closeRef}
                  type="button"
                  aria-label="Close navigation"
                  className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                  onClick={() => setMobileOpen(false)}
                >
                  <X aria-hidden className="size-5" />
                </button>
              </div>
              <nav className="flex-1 overflow-y-auto p-2">
                <NavLinks
                  role={role}
                  pathname={pathname}
                  onNavigate={() => setMobileOpen(false)}
                />
              </nav>
            </motion.aside>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center gap-3 border-b border-border bg-card px-4 md:px-6">
          <button
            type="button"
            aria-label="Open navigation"
            className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring lg:hidden"
            onClick={() => setMobileOpen(true)}
          >
            <Menu aria-hidden className="size-5" />
          </button>
          <div className="flex flex-1 items-center justify-between">
            <span className="truncate text-sm font-medium lg:hidden">{tenantName}</span>
            <span className="hidden text-sm text-muted-foreground lg:block">
              {planLabel}
            </span>
            <div className="flex items-center gap-3">
              {environment && (
                <Badge variant="warning" aria-label={`Environment: ${environment}`}>
                  {environment}
                </Badge>
              )}
              <UserMenu />
            </div>
          </div>
        </header>
        <main className="flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}

function UserMenu() {
  // Clerk's UserButton renders only when a session provider exists
  // (layout wraps with ClerkProvider when the key is configured); CI
  // builds without keys render a neutral placeholder.
  const clerkEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
  if (!clerkEnabled) {
    return (
      <div
        aria-label="User menu unavailable"
        className="size-8 rounded-full bg-muted"
        title="Sign-in not configured"
      />
    );
  }
  return <UserButton />;
}
