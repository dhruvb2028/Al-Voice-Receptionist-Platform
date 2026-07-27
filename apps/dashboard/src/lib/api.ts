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

export interface ApiErrorDetail {
  field?: string;
  issue: string;
}

export type ApiResult<T> =
  | { ok: true; data: T }
  | {
      ok: false;
      status: number;
      code: string;
      message: string;
      details?: ApiErrorDetail[];
    };

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
    let details: ApiErrorDetail[] | undefined;
    try {
      const body = (await response.json()) as {
        error?: { code?: string; message?: string; details?: ApiErrorDetail[] };
      };
      code = body.error?.code ?? code;
      message = body.error?.message ?? message;
      details = body.error?.details;
    } catch {
      // non-JSON error body — keep defaults
    }
    return { ok: false, status: response.status, code, message, details };
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

// --- Calls API types (mirror api/services/calls.py) -------------------------

export interface CallListItem {
  id: string;
  started_at: string;
  from_number_last_four: string | null;
  duration_seconds: number | null;
  outcome: string | null;
  service: string | null;
  urgency: string | null;
  booking_status: string | null;
  transfer_status: string | null;
  recording_available: boolean;
  processing_status: string;
  transport: string;
}

export interface CallListPage {
  items: CallListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface TranscriptTurn {
  turn_index: number;
  role: string;
  text: string | null;
  started_at: string | null;
  barge_in: boolean;
  interrupted: boolean;
  endpointing_ms: number | null;
  stt_finalization_ms: number | null;
  llm_ttft_ms: number | null;
  tts_ttfb_ms: number | null;
  total_latency_ms: number | null;
}

export interface ToolExecutionView {
  tool_name: string;
  status: string;
  started_at: string;
  duration_ms: number | null;
  turn_id: string | null;
  input_redacted: Record<string, unknown> | null;
  result_redacted: Record<string, unknown> | null;
  error_category: string | null;
}

export interface GuardrailEventView {
  guardrail_type: string;
  action: string;
  created_at: string;
  turn_id: string | null;
  input_redacted: Record<string, unknown> | null;
}

export interface CallBookingView {
  id: string;
  service: string | null;
  scheduled_at: string;
  timezone: string;
  status: string;
  customer_name: string | null;
}

export interface CallMessageView {
  id: string;
  customer_name: string | null;
  urgency: string;
  delivery_status: string;
  created_at: string;
}

export interface CallEscalationView {
  reason: string;
  status: string;
  initiated_at: string;
  connected_at: string | null;
  destination_last_four: string | null;
}

export interface TimelineEntry {
  at: string;
  label: string;
  kind: string;
}

export interface UsageEntry {
  usage_type: string;
  quantity: number;
  unit: string;
  cost_cents: number | null;
  provider: string | null;
}

export interface CallDetail {
  id: string;
  started_at: string;
  answered_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  direction: string;
  transport: string;
  from_number_last_four: string | null;
  to_number: string;
  outcome: string | null;
  urgency: string | null;
  recording_available: boolean;
  transcript_status: string;
  processing_status: string;
  estimated_cost_cents: number | null;
  summary: string | null;
  sentiment: string | null;
  follow_up_required: boolean | null;
  turns: TranscriptTurn[];
  tools: ToolExecutionView[];
  guardrails: GuardrailEventView[];
  booking: CallBookingView | null;
  message: CallMessageView | null;
  escalation: CallEscalationView | null;
  timeline: TimelineEntry[];
  usage: UsageEntry[];
  provider_call_sid: string | null;
  recording_status: string | null;
  failure_category: string | null;
  failure_detail_safe: string | null;
}

export interface RecordingUrlResponse {
  url: string;
  expires_seconds: number;
}

// --- Bookings & messages (mirror api/services/client_records.py) ------------

export interface BookingListItem {
  id: string;
  customer_name: string | null;
  phone_last_four: string | null;
  service: string | null;
  scheduled_at: string;
  timezone: string;
  address: string | null;
  calendar_status: string;
  status: string;
  call_id: string | null;
  created_at: string;
}

export interface BookingListPage {
  items: BookingListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface MessageListItem {
  id: string;
  customer_name: string | null;
  phone_last_four: string | null;
  body: string | null;
  urgency: string;
  created_at: string;
  call_id: string | null;
  delivery_status: string;
  reviewed_at: string | null;
  internal_note: string | null;
}

export interface MessageListPage {
  items: MessageListItem[];
  total: number;
  page: number;
  page_size: number;
}
