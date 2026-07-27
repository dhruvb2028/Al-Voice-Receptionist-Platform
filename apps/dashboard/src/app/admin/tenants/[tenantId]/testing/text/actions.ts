"use server";

import { apiFetch } from "@/lib/api";

export interface ToolTrace {
  tool_name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown> | null;
  status: string;
  duration_ms: number;
  error_category: string | null;
}

export interface GuardrailTrace {
  guardrail_type: string;
  action: string;
  detail: string | null;
}

export interface TurnTrace {
  turn_index: number;
  reply_text: string;
  phase_before: string;
  phase_after: string;
  collected: Record<string, unknown>;
  tools: ToolTrace[];
  guardrails: GuardrailTrace[];
  llm_first_token_ms: number | null;
  llm_total_ms: number | null;
  total_ms: number | null;
  failed_intent_count: number;
  escalation_reason: string | null;
  outcome: string | null;
}

export type SimResult<T> =
  | { ok: true; data: T }
  | { ok: false; message: string };

export async function startSessionAction(
  tenantId: string,
): Promise<SimResult<{ call_id: string; greeting: string }>> {
  const result = await apiFetch<{ call_id: string; greeting: string }>(
    `/admin/tenants/${tenantId}/simulator/sessions`,
    { method: "POST" },
  );
  return result.ok ? { ok: true, data: result.data } : { ok: false, message: result.message };
}

export async function sendTurnAction(
  tenantId: string,
  callId: string,
  text: string,
): Promise<SimResult<TurnTrace>> {
  const result = await apiFetch<TurnTrace>(
    `/admin/tenants/${tenantId}/simulator/sessions/${callId}/turns`,
    { method: "POST", body: JSON.stringify({ text }) },
  );
  return result.ok ? { ok: true, data: result.data } : { ok: false, message: result.message };
}

export async function setFailuresAction(
  tenantId: string,
  callId: string,
  flags: Record<string, boolean>,
): Promise<SimResult<{ flags: Record<string, boolean>; available: string[] }>> {
  const result = await apiFetch<{ flags: Record<string, boolean>; available: string[] }>(
    `/admin/tenants/${tenantId}/simulator/sessions/${callId}/failures`,
    { method: "PUT", body: JSON.stringify({ flags }) },
  );
  return result.ok ? { ok: true, data: result.data } : { ok: false, message: result.message };
}

export async function endSessionAction(
  tenantId: string,
  callId: string,
): Promise<SimResult<{ outcome: string; duration_seconds: number | null }>> {
  const result = await apiFetch<{ outcome: string; duration_seconds: number | null }>(
    `/admin/tenants/${tenantId}/simulator/sessions/${callId}/end`,
    { method: "POST", body: JSON.stringify({}) },
  );
  return result.ok ? { ok: true, data: result.data } : { ok: false, message: result.message };
}
