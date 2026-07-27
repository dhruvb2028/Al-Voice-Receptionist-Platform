"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  endSessionAction,
  sendTurnAction,
  setFailuresAction,
  startSessionAction,
  type TurnTrace,
} from "./actions";

interface ChatMessage {
  role: "caller" | "assistant";
  text: string;
}

const FAILURE_OPTIONS: Array<{ flag: string; label: string }> = [
  { flag: "calendar_timeout", label: "Calendar timeout" },
  { flag: "calendar_auth_failure", label: "Calendar auth failure" },
  { flag: "booking_duplicate", label: "Booking duplicate" },
  { flag: "llm_timeout", label: "LLM timeout" },
  { flag: "tool_failure", label: "Tool failure" },
  { flag: "transfer_failure", label: "Transfer failure" },
  { flag: "notification_failure", label: "Notification failure" },
  { flag: "max_call_duration", label: "Max call duration" },
];

export function SimulatorConsole({ tenantId }: { tenantId: string }) {
  const [callId, setCallId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [lastTrace, setLastTrace] = useState<TurnTrace | null>(null);
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [ended, setEnded] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [showRawTrace, setShowRawTrace] = useState(false);
  const [pending, startTransition] = useTransition();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  function start() {
    startTransition(async () => {
      setError(null);
      const result = await startSessionAction(tenantId);
      if (!result.ok) {
        setError(result.message);
        return;
      }
      setCallId(result.data.call_id);
      setMessages([{ role: "assistant", text: result.data.greeting }]);
      setLastTrace(null);
      setFlags({});
      setEnded(null);
    });
  }

  function send() {
    const text = input.trim();
    if (!text || !callId) return;
    setInput("");
    setMessages((current) => [...current, { role: "caller", text }]);
    startTransition(async () => {
      const result = await sendTurnAction(tenantId, callId, text);
      if (!result.ok) {
        setError(result.message);
        return;
      }
      setError(null);
      setLastTrace(result.data);
      setMessages((current) => [
        ...current,
        { role: "assistant", text: result.data.reply_text },
      ]);
    });
  }

  function toggleFlag(flag: string) {
    if (!callId) return;
    const next = { ...flags, [flag]: !flags[flag] };
    setFlags(next);
    startTransition(async () => {
      const result = await setFailuresAction(tenantId, callId, { [flag]: next[flag] });
      if (!result.ok) setError(result.message);
    });
  }

  function end() {
    if (!callId) return;
    startTransition(async () => {
      const result = await endSessionAction(tenantId, callId);
      if (!result.ok) {
        setError(result.message);
        return;
      }
      setEnded(result.data.outcome);
      setCallId(null);
    });
  }

  const collectedEntries = lastTrace
    ? Object.entries(lastTrace.collected).filter(([, value]) => value)
    : [];

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
      {/* Conversation column */}
      <Card className="flex min-h-[480px] flex-col">
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Conversation</CardTitle>
          <div className="flex gap-2">
            {callId ? (
              <Button size="sm" variant="destructive" onClick={end} disabled={pending}>
                End test
              </Button>
            ) : (
              <Button size="sm" onClick={start} disabled={pending}>
                {pending ? "Starting…" : ended ? "Start new test" : "Start test session"}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="flex flex-1 flex-col">
          {error && (
            <p role="alert" className="mb-2 text-sm text-destructive">
              {error}
            </p>
          )}
          {ended && (
            <p role="status" className="mb-2 text-sm font-medium text-accent">
              Session ended — outcome: {ended.replaceAll("_", " ")}. Turns and tool calls
              were persisted to the call record.
            </p>
          )}
          <div
            ref={scrollRef}
            className="flex-1 space-y-2 overflow-y-auto rounded-md bg-muted/40 p-3"
            aria-live="polite"
          >
            {messages.length === 0 && !callId && (
              <p className="text-sm text-muted-foreground">
                Start a session to talk to this tenant&apos;s receptionist as a caller
                would.
              </p>
            )}
            {messages.map((message, index) => (
              <div
                key={index}
                className={
                  message.role === "caller" ? "flex justify-end" : "flex justify-start"
                }
              >
                <div
                  className={
                    message.role === "caller"
                      ? "max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-on-primary"
                      : "max-w-[80%] rounded-lg border border-border bg-card px-3 py-2 text-sm"
                  }
                >
                  {message.text}
                </div>
              </div>
            ))}
          </div>
          <form
            className="mt-3 flex gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              send();
            }}
          >
            <label htmlFor="caller-input" className="sr-only">
              Caller message
            </label>
            <Input
              id="caller-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={callId ? "Type what the caller says…" : "Start a session first"}
              disabled={!callId || pending}
            />
            <Button type="submit" disabled={!callId || pending || !input.trim()}>
              Send
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* Inspector column */}
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>State</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {lastTrace ? (
              <>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Phase</span>
                  <Badge variant="info">{lastTrace.phase_after}</Badge>
                </div>
                {lastTrace.escalation_reason && (
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">Escalation</span>
                    <Badge variant="danger">{lastTrace.escalation_reason}</Badge>
                  </div>
                )}
                {lastTrace.outcome && (
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">Outcome</span>
                    <Badge variant="success">{lastTrace.outcome.replaceAll("_", " ")}</Badge>
                  </div>
                )}
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Failed intents</span>
                  <span className="tabular-nums">{lastTrace.failed_intent_count}</span>
                </div>
                <div className="border-t border-border pt-2">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Collected fields
                  </p>
                  {collectedEntries.length === 0 ? (
                    <p className="mt-1 text-xs text-muted-foreground">None yet</p>
                  ) : (
                    <dl className="mt-1 space-y-0.5">
                      {collectedEntries.map(([key, value]) => (
                        <div key={key} className="flex justify-between text-xs">
                          <dt className="text-muted-foreground">
                            {key.replaceAll("_", " ")}
                          </dt>
                          <dd>{String(value)}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </div>
                <div className="border-t border-border pt-2">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Latency
                  </p>
                  <dl className="mt-1 space-y-0.5 text-xs">
                    <div className="flex justify-between">
                      <dt className="text-muted-foreground">LLM first token</dt>
                      <dd className="tabular-nums">
                        {lastTrace.llm_first_token_ms ?? "—"} ms
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted-foreground">LLM total</dt>
                      <dd className="tabular-nums">{lastTrace.llm_total_ms ?? "—"} ms</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted-foreground">Turn total</dt>
                      <dd className="tabular-nums">{lastTrace.total_ms ?? "—"} ms</dd>
                    </div>
                  </dl>
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                Send a turn to inspect engine state.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Tools &amp; guardrails</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {lastTrace && lastTrace.tools.length > 0 ? (
              <ul className="space-y-1.5">
                {lastTrace.tools.map((tool, index) => (
                  <li key={index} className="flex items-center justify-between text-xs">
                    <code className="rounded bg-muted px-1 py-0.5">{tool.tool_name}</code>
                    <span className="flex items-center gap-1.5">
                      <Badge
                        variant={tool.status === "success" ? "success" : "danger"}
                      >
                        {tool.status}
                      </Badge>
                      <span className="tabular-nums text-muted-foreground">
                        {tool.duration_ms} ms
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">No tool calls this turn.</p>
            )}
            {lastTrace && lastTrace.guardrails.length > 0 && (
              <ul className="space-y-1 border-t border-border pt-2">
                {lastTrace.guardrails.map((guardrail, index) => (
                  <li key={index} className="flex items-center justify-between text-xs">
                    <span>{guardrail.guardrail_type.replaceAll("_", " ")}</span>
                    <Badge variant="warning">{guardrail.action}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Failure simulation</CardTitle>
          </CardHeader>
          <CardContent>
            <fieldset disabled={!callId || pending} className="space-y-1.5">
              {FAILURE_OPTIONS.map((option) => (
                <label
                  key={option.flag}
                  className="flex items-center gap-2 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={Boolean(flags[option.flag])}
                    onChange={() => toggleFlag(option.flag)}
                  />
                  {option.label}
                </label>
              ))}
            </fieldset>
            <p className="mt-2 text-xs text-muted-foreground">
              Emergency phrases, human requests, and out-of-scope questions are
              triggered by typing them as the caller.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex items-center justify-between">
            <CardTitle>Raw technical trace</CardTitle>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowRawTrace((value) => !value)}
              aria-expanded={showRawTrace}
            >
              {showRawTrace ? "Hide" : "Show"}
            </Button>
          </CardHeader>
          {showRawTrace && (
            <CardContent>
              <pre className="max-h-64 overflow-auto rounded-md bg-muted p-2 text-xs">
                {lastTrace ? JSON.stringify(lastTrace, null, 2) : "No turn yet."}
              </pre>
            </CardContent>
          )}
        </Card>
      </div>
    </div>
  );
}
