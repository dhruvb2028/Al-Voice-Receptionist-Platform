"use client";

import { motion, useReducedMotion } from "framer-motion";
import type {
  CallEscalationView,
  GuardrailEventView,
  ToolExecutionView,
  TranscriptTurn,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface TranscriptProps {
  turns: TranscriptTurn[];
  tools: ToolExecutionView[];
  guardrails: GuardrailEventView[];
  escalation: CallEscalationView | null;
  admin?: boolean;
}

type Row =
  | { kind: "turn"; turn: TranscriptTurn }
  | { kind: "tool"; tool: ToolExecutionView }
  | { kind: "guardrail"; event: GuardrailEventView }
  | { kind: "escalation"; escalation: CallEscalationView };

/** Conversation first, then tool activity, guardrail interventions, and
 *  the escalation note — each styled distinctly. */
function buildRows(props: TranscriptProps): Row[] {
  const rows: Row[] = [];
  for (const turn of props.turns) {
    rows.push({ kind: "turn", turn });
  }
  for (const tool of props.tools) {
    rows.push({ kind: "tool", tool });
  }
  for (const event of props.guardrails) {
    rows.push({ kind: "guardrail", event });
  }
  if (props.escalation) {
    rows.push({ kind: "escalation", escalation: props.escalation });
  }
  return rows;
}

function TurnBubble({ turn }: { turn: TranscriptTurn }) {
  const isCaller = turn.role === "caller";
  return (
    <div className={cn("flex", isCaller ? "justify-start" : "justify-end")}>
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-3 py-2 text-sm",
          isCaller ? "bg-muted" : "bg-primary/10",
        )}
      >
        <p className="mb-0.5 text-xs font-medium text-muted-foreground">
          {isCaller ? "Caller" : "Receptionist"}
          {turn.interrupted && (
            <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
              interrupted
            </span>
          )}
          {turn.total_latency_ms !== null && (
            <span className="ml-2 tabular-nums text-[10px]">
              {turn.total_latency_ms}ms
            </span>
          )}
        </p>
        <p className="whitespace-pre-wrap">{turn.text ?? "…"}</p>
      </div>
    </div>
  );
}

function ToolRow({ tool, admin }: { tool: ToolExecutionView; admin?: boolean }) {
  return (
    <div className="flex justify-center">
      <div className="w-full max-w-[85%] rounded-md border border-dashed border-border bg-card px-3 py-1.5 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">
          ⚙ {tool.tool_name.replaceAll("_", " ")}
        </span>
        <span
          className={cn(
            "ml-2",
            tool.status === "success" ? "text-emerald-700" : "text-red-700",
          )}
        >
          {tool.status}
        </span>
        {tool.duration_ms !== null && (
          <span className="ml-2 tabular-nums">{tool.duration_ms}ms</span>
        )}
        {admin && tool.result_redacted && (
          <pre className="mt-1 overflow-x-auto rounded bg-muted p-1.5 text-[11px]">
            {JSON.stringify(tool.result_redacted)}
          </pre>
        )}
      </div>
    </div>
  );
}

function GuardrailRow({ event, admin }: { event: GuardrailEventView; admin?: boolean }) {
  return (
    <div className="flex justify-center">
      <div className="w-full max-w-[85%] rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-900">
        <span className="font-medium">
          ⛨ Guardrail: {event.guardrail_type.replaceAll("_", " ")}
        </span>
        <span className="ml-2">{event.action}</span>
        {admin && event.input_redacted && (
          <pre className="mt-1 overflow-x-auto rounded bg-amber-100/60 p-1.5 text-[11px]">
            {JSON.stringify(event.input_redacted)}
          </pre>
        )}
      </div>
    </div>
  );
}

function EscalationRow({ escalation }: { escalation: CallEscalationView }) {
  return (
    <div className="flex justify-center">
      <div className="w-full max-w-[85%] rounded-md border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs text-blue-900">
        <span className="font-medium">
          ↗ Transferred to a human ({escalation.reason.replaceAll("_", " ")})
        </span>
        <span className="ml-2">{escalation.status.replaceAll("_", " ")}</span>
      </div>
    </div>
  );
}

export function Transcript(props: TranscriptProps) {
  const reduceMotion = useReducedMotion();
  const rows = buildRows(props);

  if (rows.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        No transcript is available for this call.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {rows.map((row, index) => (
        <motion.div
          key={index}
          initial={reduceMotion ? false : { opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18, delay: Math.min(index * 0.03, 0.4) }}
        >
          {row.kind === "turn" && <TurnBubble turn={row.turn} />}
          {row.kind === "tool" && <ToolRow tool={row.tool} admin={props.admin} />}
          {row.kind === "guardrail" && (
            <GuardrailRow event={row.event} admin={props.admin} />
          )}
          {row.kind === "escalation" && <EscalationRow escalation={row.escalation} />}
        </motion.div>
      ))}
    </div>
  );
}
