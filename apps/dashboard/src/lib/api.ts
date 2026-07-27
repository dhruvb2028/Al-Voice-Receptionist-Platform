/**
 * Server-side API client. Attaches the Clerk session token; never runs
 * in the browser (import from server components and server actions only).
 *
 * Returns a discriminated result instead of throwing so pages can render
 * explicit error / permission-denied states.
 */
import "server-only";
import { auth } from "@clerk/nextjs/server";
import { serverEnv } from "@/env";

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; code: string; message: string };

async function getToken(): Promise<string | null> {
  if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) return null;
  const session = await auth();
  return session.getToken();
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit & { searchParams?: Record<string, string | undefined> },
): Promise<ApiResult<T>> {
  const token = await getToken();
  if (!token) {
    return {
      ok: false,
      status: 401,
      code: "not_authenticated",
      message: "Sign in to continue.",
    };
  }

  const url = new URL(path, serverEnv.API_BASE_URL);
  for (const [key, value] of Object.entries(init?.searchParams ?? {})) {
    if (value) url.searchParams.set(key, value);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        ...init?.headers,
      },
      cache: "no-store",
    });
  } catch {
    return {
      ok: false,
      status: 0,
      code: "network_error",
      message: "The service could not be reached.",
    };
  }

  if (!response.ok) {
    let code = "request_failed";
    let message = "The request failed.";
    try {
      const body = (await response.json()) as {
        error?: { code?: string; message?: string };
      };
      code = body.error?.code ?? code;
      message = body.error?.message ?? message;
    } catch {
      // non-JSON error body — keep defaults
    }
    return { ok: false, status: response.status, code, message };
  }

  return { ok: true, data: (await response.json()) as T };
}

// --- Admin API types (mirror services/api schemas) -------------------------

export interface TenantListItem {
  id: string;
  name: string;
  slug: string;
  vertical: string;
  status: string;
  assigned_numbers: number;
  calls_today: number;
  calls_this_month: number;
  failed_calls_this_month: number;
  calendar_health: string;
  last_successful_call_at: string | null;
  usage_minutes_this_month: number;
  configuration_ready: boolean;
}

export interface TenantListResponse {
  items: TenantListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminTenantView {
  id: string;
  name: string;
  slug: string;
  status: string;
  vertical: string;
  timezone: string;
  country: string;
  expected_monthly_calls: number | null;
  external_auth_org_id: string | null;
}

export interface ActivationBlocker {
  code: string;
  message: string;
  waivable: boolean;
}

export interface ActivationReadiness {
  tenant_id: string;
  ready: boolean;
  blockers: ActivationBlocker[];
}
