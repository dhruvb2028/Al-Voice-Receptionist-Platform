"use client";

import { useMemo, useState, useTransition } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import {
  approveAction,
  rejectAction,
  rollbackAction,
  saveDraftAction,
  submitDraftAction,
  type ActionResult,
} from "./actions";

export interface ConfigVersionSummary {
  id: string;
  version: number;
  state: string;
  created_by: string;
  reviewed_by: string | null;
  review_notes: string | null;
  created_at: string;
}

interface ConfigVersionDetail extends ConfigVersionSummary {
  payload: Record<string, unknown>;
}

interface Props {
  tenantId: string;
  draft: ConfigVersionDetail | null;
  active: ConfigVersionDetail | null;
  versions: ConfigVersionSummary[];
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/* Editable working model — mirrors ReceptionistConfig. The API is the
   validator of record; this component only shapes the data. */
interface WorkingConfig {
  identity: {
    business_name: string;
    timezone: string;
    address: string;
    service_region_label: string;
    business_phone: string;
    website: string;
    emergency_contact: string;
  };
  greeting: {
    greeting: string;
    recording_notice: string;
    after_hours_greeting: string;
    tenant_approved: boolean;
  };
  services: Array<{
    name: string;
    description: string;
    duration_minutes: number;
    category: string;
    active: boolean;
  }>;
  prices: Array<{
    service_name: string;
    label: string;
    minimum_amount_cents: number | null;
    maximum_amount_cents: number | null;
    unit: string;
    customer_visible: boolean;
    approved: boolean;
  }>;
  hours: Array<{
    weekday: number;
    closed: boolean;
    opens_at: string;
    closes_at: string;
  }>;
  service_area: { postal_codes: string; cities: string; radius_miles: string; exclusions: string };
  escalation: {
    emergency_destination: string;
    human_request_behavior: string;
    after_hours_destination: string;
    failed_intent_threshold: number;
    transfer_timeout_seconds: number;
  };
  voice: {
    voice_id: string;
    speaking_style: string;
    filler_phrases: string;
    max_call_seconds: number;
  };
}

function emptyWorking(): WorkingConfig {
  return {
    identity: {
      business_name: "",
      timezone: "America/New_York",
      address: "",
      service_region_label: "",
      business_phone: "",
      website: "",
      emergency_contact: "",
    },
    greeting: {
      greeting: "",
      recording_notice: "",
      after_hours_greeting: "",
      tenant_approved: false,
    },
    services: [],
    prices: [],
    hours: WEEKDAYS.map((_, weekday) => ({
      weekday,
      closed: weekday === 6,
      opens_at: "08:00",
      closes_at: "17:00",
    })),
    service_area: { postal_codes: "", cities: "", radius_miles: "", exclusions: "" },
    escalation: {
      emergency_destination: "",
      human_request_behavior: "transfer",
      after_hours_destination: "",
      failed_intent_threshold: 2,
      transfer_timeout_seconds: 25,
    },
    voice: { voice_id: "", speaking_style: "", filler_phrases: "", max_call_seconds: 900 },
  };
}

/* payload <-> working conversions */
function fromPayload(payload: Record<string, unknown>): WorkingConfig {
  const p = payload as never as {
    identity: Partial<WorkingConfig["identity"]>;
    greeting: Partial<WorkingConfig["greeting"]>;
    services: WorkingConfig["services"];
    prices: WorkingConfig["prices"];
    hours: Array<{ weekday: number; closed?: boolean; opens_at?: string; closes_at?: string }>;
    service_area: {
      postal_codes?: string[];
      cities?: string[];
      radius_miles?: number | null;
      exclusions?: string[];
    };
    escalation: Partial<WorkingConfig["escalation"]>;
    voice: Partial<WorkingConfig["voice"]> & { filler_phrases?: string[] };
  };
  const base = emptyWorking();
  return {
    identity: { ...base.identity, ...stripNull(p.identity) },
    greeting: { ...base.greeting, ...stripNull(p.greeting) },
    services: (p.services ?? []).map((s) => ({
      name: s.name ?? "",
      description: s.description ?? "",
      duration_minutes: s.duration_minutes ?? 60,
      category: s.category ?? "",
      active: s.active ?? true,
    })),
    prices: (p.prices ?? []).map((price) => ({
      service_name: price.service_name ?? "",
      label: price.label ?? "",
      minimum_amount_cents: price.minimum_amount_cents ?? null,
      maximum_amount_cents: price.maximum_amount_cents ?? null,
      unit: price.unit ?? "flat",
      customer_visible: price.customer_visible ?? true,
      approved: price.approved ?? false,
    })),
    hours: base.hours.map((day) => {
      const found = (p.hours ?? []).find((h) => h.weekday === day.weekday);
      if (!found) return day;
      return {
        weekday: day.weekday,
        closed: found.closed ?? false,
        opens_at: (found.opens_at ?? "08:00").slice(0, 5),
        closes_at: (found.closes_at ?? "17:00").slice(0, 5),
      };
    }),
    service_area: {
      postal_codes: (p.service_area?.postal_codes ?? []).join(", "),
      cities: (p.service_area?.cities ?? []).join(", "),
      radius_miles: p.service_area?.radius_miles ? String(p.service_area.radius_miles) : "",
      exclusions: (p.service_area?.exclusions ?? []).join(", "),
    },
    escalation: { ...base.escalation, ...stripNull(p.escalation) },
    voice: {
      ...base.voice,
      ...stripNull({ ...p.voice, filler_phrases: undefined }),
      filler_phrases: (p.voice?.filler_phrases ?? []).join(", "),
    },
  };
}

function stripNull<T extends object>(value: T | undefined): Partial<T> {
  if (!value) return {};
  return Object.fromEntries(
    Object.entries(value).filter(([, v]) => v !== null && v !== undefined),
  ) as Partial<T>;
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function toPayload(working: WorkingConfig): Record<string, unknown> {
  const orNull = (value: string) => (value.trim() ? value.trim() : null);
  return {
    identity: {
      business_name: working.identity.business_name,
      timezone: working.identity.timezone,
      address: orNull(working.identity.address),
      service_region_label: orNull(working.identity.service_region_label),
      business_phone: orNull(working.identity.business_phone),
      website: orNull(working.identity.website),
      emergency_contact: orNull(working.identity.emergency_contact),
    },
    greeting: {
      greeting: working.greeting.greeting,
      recording_notice: orNull(working.greeting.recording_notice),
      after_hours_greeting: orNull(working.greeting.after_hours_greeting),
      tenant_approved: working.greeting.tenant_approved,
    },
    services: working.services.map((s) => ({
      name: s.name,
      description: orNull(s.description),
      duration_minutes: Number(s.duration_minutes),
      category: orNull(s.category),
      active: s.active,
    })),
    prices: working.prices.map((p) => ({
      service_name: p.service_name,
      label: p.label,
      minimum_amount_cents: p.minimum_amount_cents,
      maximum_amount_cents: p.maximum_amount_cents,
      unit: p.unit,
      customer_visible: p.customer_visible,
      approved: p.approved,
    })),
    hours: working.hours.map((h) =>
      h.closed
        ? { weekday: h.weekday, closed: true }
        : { weekday: h.weekday, opens_at: h.opens_at, closes_at: h.closes_at },
    ),
    holiday_overrides: [],
    service_area: {
      postal_codes: splitList(working.service_area.postal_codes),
      cities: splitList(working.service_area.cities),
      radius_miles: working.service_area.radius_miles
        ? Number(working.service_area.radius_miles)
        : null,
      exclusions: splitList(working.service_area.exclusions),
    },
    escalation: {
      emergency_destination: working.escalation.emergency_destination,
      human_request_behavior: working.escalation.human_request_behavior,
      after_hours_destination: orNull(working.escalation.after_hours_destination),
      failed_intent_threshold: Number(working.escalation.failed_intent_threshold),
      transfer_timeout_seconds: Number(working.escalation.transfer_timeout_seconds),
      message_fallback: true,
    },
    voice: {
      voice_id: working.voice.voice_id,
      speaking_style: orNull(working.voice.speaking_style),
      language: "en",
      filler_phrases: splitList(working.voice.filler_phrases),
      max_call_seconds: Number(working.voice.max_call_seconds),
    },
  };
}

function centsToDollars(cents: number | null): string {
  return cents === null ? "" : (cents / 100).toFixed(2);
}

function dollarsToCents(value: string): number | null {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? Math.round(parsed * 100) : null;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">{children}</CardContent>
    </Card>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="font-medium">{label}</span>
      {children}
    </label>
  );
}

export function ConfigWorkbench({ tenantId, draft, active, versions }: Props) {
  const initial = useMemo(() => {
    if (draft) return fromPayload(draft.payload);
    if (active) return fromPayload(active.payload);
    return emptyWorking();
  }, [draft, active]);

  const [working, setWorking] = useState<WorkingConfig>(initial);
  const [result, setResult] = useState<ActionResult | null>(null);
  const [pending, startTransition] = useTransition();
  const [confirmingApprove, setConfirmingApprove] = useState(false);
  const [rejectNotes, setRejectNotes] = useState("");
  const [rollbackTarget, setRollbackTarget] = useState<number | null>(null);

  const pendingReview = draft?.state === "pending_review";

  function update<K extends keyof WorkingConfig>(key: K, value: WorkingConfig[K]) {
    setWorking((current) => ({ ...current, [key]: value }));
  }

  function run(action: () => Promise<ActionResult>) {
    startTransition(async () => {
      setResult(await action());
      setConfirmingApprove(false);
      setRollbackTarget(null);
    });
  }

  return (
    <div className="space-y-4">
      {/* Status banner */}
      <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex items-center gap-3 text-sm">
          <span>
            Active version:{" "}
            {active ? (
              <Badge variant="success">v{active.version}</Badge>
            ) : (
              <Badge variant="warning">none — nothing is live yet</Badge>
            )}
          </span>
          {draft && (
            <span>
              Open draft:{" "}
              <Badge variant={pendingReview ? "info" : "neutral"}>
                v{draft.version} · {draft.state.replaceAll("_", " ")}
              </Badge>
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {!pendingReview && (
            <Button
              size="sm"
              variant="secondary"
              disabled={pending}
              onClick={() => run(() => saveDraftAction(tenantId, toPayload(working)))}
            >
              Save draft
            </Button>
          )}
          {draft && !pendingReview && (
            <Button size="sm" disabled={pending} onClick={() => run(() => submitDraftAction(tenantId))}>
              Submit for review
            </Button>
          )}
          {pendingReview && (
            <>
              <Button size="sm" disabled={pending} onClick={() => setConfirmingApprove(true)}>
                Approve &amp; apply
              </Button>
              <Button
                size="sm"
                variant="destructive"
                disabled={pending || rejectNotes.trim().length < 3}
                title={rejectNotes.trim().length < 3 ? "Add review notes first" : undefined}
                onClick={() => run(() => rejectAction(tenantId, rejectNotes.trim()))}
              >
                Reject
              </Button>
            </>
          )}
        </div>
      </Card>

      {result && (
        <p
          role="status"
          className={result.ok ? "text-sm font-medium text-accent" : "text-sm text-destructive"}
        >
          {result.message}
          {result.details?.slice(0, 5).map((d) => (
            <span key={`${d.field}-${d.issue}`} className="block text-xs">
              {d.field ? `${d.field}: ` : ""}
              {d.issue}
            </span>
          ))}
        </p>
      )}

      {pendingReview && (
        <Card className="p-4">
          <Row label="Review notes (required to reject)">
            <Input
              value={rejectNotes}
              onChange={(e) => setRejectNotes(e.target.value)}
              placeholder="What must change before approval?"
            />
          </Row>
        </Card>
      )}

      <fieldset disabled={pendingReview || pending} className="space-y-4">
        <Section title="Business identity">
          <div className="grid gap-3 sm:grid-cols-2">
            <Row label="Business name">
              <Input
                value={working.identity.business_name}
                onChange={(e) =>
                  update("identity", { ...working.identity, business_name: e.target.value })
                }
              />
            </Row>
            <Row label="Timezone (IANA)">
              <Input
                value={working.identity.timezone}
                onChange={(e) =>
                  update("identity", { ...working.identity, timezone: e.target.value })
                }
              />
            </Row>
            <Row label="Address">
              <Input
                value={working.identity.address}
                onChange={(e) =>
                  update("identity", { ...working.identity, address: e.target.value })
                }
              />
            </Row>
            <Row label="Service region label">
              <Input
                value={working.identity.service_region_label}
                onChange={(e) =>
                  update("identity", {
                    ...working.identity,
                    service_region_label: e.target.value,
                  })
                }
              />
            </Row>
            <Row label="Business phone (+E.164)">
              <Input
                value={working.identity.business_phone}
                onChange={(e) =>
                  update("identity", { ...working.identity, business_phone: e.target.value })
                }
              />
            </Row>
            <Row label="Website">
              <Input
                value={working.identity.website}
                onChange={(e) =>
                  update("identity", { ...working.identity, website: e.target.value })
                }
              />
            </Row>
            <Row label="Emergency contact (+E.164)">
              <Input
                value={working.identity.emergency_contact}
                onChange={(e) =>
                  update("identity", {
                    ...working.identity,
                    emergency_contact: e.target.value,
                  })
                }
              />
            </Row>
          </div>
        </Section>

        <Section title="Greeting">
          <Row label="Greeting (spoken exactly as written)">
            <textarea
              className="min-h-20 w-full rounded-md border border-border bg-card p-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"
              value={working.greeting.greeting}
              onChange={(e) => update("greeting", { ...working.greeting, greeting: e.target.value })}
            />
          </Row>
          <Row label="Recording notice (empty disables recording)">
            <Input
              value={working.greeting.recording_notice}
              onChange={(e) =>
                update("greeting", { ...working.greeting, recording_notice: e.target.value })
              }
            />
          </Row>
          <Row label="After-hours greeting">
            <Input
              value={working.greeting.after_hours_greeting}
              onChange={(e) =>
                update("greeting", {
                  ...working.greeting,
                  after_hours_greeting: e.target.value,
                })
              }
            />
          </Row>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={working.greeting.tenant_approved}
              onChange={(e) =>
                update("greeting", { ...working.greeting, tenant_approved: e.target.checked })
              }
            />
            The client has approved this greeting text
          </label>
        </Section>

        <Section title="Services">
          {working.services.map((service, index) => (
            <div key={index} className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-5">
              <Input
                aria-label="Service name"
                placeholder="Name"
                value={service.name}
                onChange={(e) => {
                  const services = [...working.services];
                  services[index] = { ...service, name: e.target.value };
                  update("services", services);
                }}
              />
              <Input
                aria-label="Description"
                placeholder="Description"
                className="sm:col-span-2"
                value={service.description}
                onChange={(e) => {
                  const services = [...working.services];
                  services[index] = { ...service, description: e.target.value };
                  update("services", services);
                }}
              />
              <Input
                aria-label="Duration minutes"
                type="number"
                min={15}
                max={480}
                value={service.duration_minutes}
                onChange={(e) => {
                  const services = [...working.services];
                  services[index] = { ...service, duration_minutes: Number(e.target.value) };
                  update("services", services);
                }}
              />
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="checkbox"
                    checked={service.active}
                    onChange={(e) => {
                      const services = [...working.services];
                      services[index] = { ...service, active: e.target.checked };
                      update("services", services);
                    }}
                  />
                  Active
                </label>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    update(
                      "services",
                      working.services.filter((_, i) => i !== index),
                    )
                  }
                >
                  Remove
                </Button>
              </div>
            </div>
          ))}
          <Button
            size="sm"
            variant="secondary"
            onClick={() =>
              update("services", [
                ...working.services,
                { name: "", description: "", duration_minutes: 60, category: "", active: true },
              ])
            }
          >
            Add service
          </Button>
        </Section>

        <Section title="Prices">
          {working.prices.map((price, index) => (
            <div key={index} className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-6">
              <select
                aria-label="Service"
                className="h-10 rounded-md border border-border bg-card px-2 text-sm"
                value={price.service_name}
                onChange={(e) => {
                  const prices = [...working.prices];
                  prices[index] = { ...price, service_name: e.target.value };
                  update("prices", prices);
                }}
              >
                <option value="">Select service…</option>
                {working.services.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name}
                  </option>
                ))}
              </select>
              <Input
                aria-label="Label"
                placeholder="Label"
                value={price.label}
                onChange={(e) => {
                  const prices = [...working.prices];
                  prices[index] = { ...price, label: e.target.value };
                  update("prices", prices);
                }}
              />
              <Input
                aria-label="Minimum dollars"
                type="number"
                step="0.01"
                placeholder="Min $"
                value={centsToDollars(price.minimum_amount_cents)}
                onChange={(e) => {
                  const prices = [...working.prices];
                  prices[index] = { ...price, minimum_amount_cents: dollarsToCents(e.target.value) };
                  update("prices", prices);
                }}
              />
              <Input
                aria-label="Maximum dollars"
                type="number"
                step="0.01"
                placeholder="Max $"
                value={centsToDollars(price.maximum_amount_cents)}
                onChange={(e) => {
                  const prices = [...working.prices];
                  prices[index] = { ...price, maximum_amount_cents: dollarsToCents(e.target.value) };
                  update("prices", prices);
                }}
              />
              <select
                aria-label="Unit"
                className="h-10 rounded-md border border-border bg-card px-2 text-sm"
                value={price.unit}
                onChange={(e) => {
                  const prices = [...working.prices];
                  prices[index] = { ...price, unit: e.target.value };
                  update("prices", prices);
                }}
              >
                <option value="flat">flat</option>
                <option value="range">range</option>
                <option value="hourly">hourly</option>
                <option value="per_visit">per visit</option>
              </select>
              <div className="flex items-center gap-2 text-xs">
                <label className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={price.approved}
                    onChange={(e) => {
                      const prices = [...working.prices];
                      prices[index] = { ...price, approved: e.target.checked };
                      update("prices", prices);
                    }}
                  />
                  Approved
                </label>
                <label className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={price.customer_visible}
                    onChange={(e) => {
                      const prices = [...working.prices];
                      prices[index] = { ...price, customer_visible: e.target.checked };
                      update("prices", prices);
                    }}
                  />
                  Speakable
                </label>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    update(
                      "prices",
                      working.prices.filter((_, i) => i !== index),
                    )
                  }
                >
                  Remove
                </Button>
              </div>
            </div>
          ))}
          <Button
            size="sm"
            variant="secondary"
            onClick={() =>
              update("prices", [
                ...working.prices,
                {
                  service_name: working.services[0]?.name ?? "",
                  label: "",
                  minimum_amount_cents: null,
                  maximum_amount_cents: null,
                  unit: "flat",
                  customer_visible: true,
                  approved: false,
                },
              ])
            }
          >
            Add price
          </Button>
          <p className="text-xs text-muted-foreground">
            The receptionist can only quote approved, speakable prices — never invented ones.
          </p>
        </Section>

        <Section title="Business hours">
          <div className="space-y-2">
            {working.hours.map((day, index) => (
              <div key={day.weekday} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="w-24 font-medium">{WEEKDAYS[day.weekday]}</span>
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="checkbox"
                    checked={day.closed}
                    onChange={(e) => {
                      const hours = [...working.hours];
                      hours[index] = { ...day, closed: e.target.checked };
                      update("hours", hours);
                    }}
                  />
                  Closed
                </label>
                {!day.closed && (
                  <>
                    <Input
                      aria-label={`${WEEKDAYS[day.weekday]} opens at`}
                      type="time"
                      className="w-32"
                      value={day.opens_at}
                      onChange={(e) => {
                        const hours = [...working.hours];
                        hours[index] = { ...day, opens_at: e.target.value };
                        update("hours", hours);
                      }}
                    />
                    <span className="text-muted-foreground">to</span>
                    <Input
                      aria-label={`${WEEKDAYS[day.weekday]} closes at`}
                      type="time"
                      className="w-32"
                      value={day.closes_at}
                      onChange={(e) => {
                        const hours = [...working.hours];
                        hours[index] = { ...day, closes_at: e.target.value };
                        update("hours", hours);
                      }}
                    />
                  </>
                )}
              </div>
            ))}
          </div>
        </Section>

        <Section title="Service area">
          <div className="grid gap-3 sm:grid-cols-2">
            <Row label="Postal codes (comma-separated)">
              <Input
                value={working.service_area.postal_codes}
                onChange={(e) =>
                  update("service_area", {
                    ...working.service_area,
                    postal_codes: e.target.value,
                  })
                }
              />
            </Row>
            <Row label="Cities (comma-separated)">
              <Input
                value={working.service_area.cities}
                onChange={(e) =>
                  update("service_area", { ...working.service_area, cities: e.target.value })
                }
              />
            </Row>
            <Row label="Radius (miles, optional)">
              <Input
                type="number"
                value={working.service_area.radius_miles}
                onChange={(e) =>
                  update("service_area", {
                    ...working.service_area,
                    radius_miles: e.target.value,
                  })
                }
              />
            </Row>
            <Row label="Exclusions (comma-separated)">
              <Input
                value={working.service_area.exclusions}
                onChange={(e) =>
                  update("service_area", {
                    ...working.service_area,
                    exclusions: e.target.value,
                  })
                }
              />
            </Row>
          </div>
        </Section>

        <Section title="Escalation policy">
          <div className="grid gap-3 sm:grid-cols-2">
            <Row label="Emergency destination (+E.164)">
              <Input
                value={working.escalation.emergency_destination}
                onChange={(e) =>
                  update("escalation", {
                    ...working.escalation,
                    emergency_destination: e.target.value,
                  })
                }
              />
            </Row>
            <Row label="After-hours destination (optional)">
              <Input
                value={working.escalation.after_hours_destination}
                onChange={(e) =>
                  update("escalation", {
                    ...working.escalation,
                    after_hours_destination: e.target.value,
                  })
                }
              />
            </Row>
            <Row label="Human-request behaviour">
              <select
                className="h-10 w-full rounded-md border border-border bg-card px-2 text-sm"
                value={working.escalation.human_request_behavior}
                onChange={(e) =>
                  update("escalation", {
                    ...working.escalation,
                    human_request_behavior: e.target.value,
                  })
                }
              >
                <option value="transfer">Transfer immediately</option>
                <option value="message">Take a message</option>
              </select>
            </Row>
            <Row label="Failed-intent threshold">
              <Input
                type="number"
                min={1}
                max={5}
                value={working.escalation.failed_intent_threshold}
                onChange={(e) =>
                  update("escalation", {
                    ...working.escalation,
                    failed_intent_threshold: Number(e.target.value),
                  })
                }
              />
            </Row>
            <Row label="Transfer timeout (seconds)">
              <Input
                type="number"
                min={10}
                max={120}
                value={working.escalation.transfer_timeout_seconds}
                onChange={(e) =>
                  update("escalation", {
                    ...working.escalation,
                    transfer_timeout_seconds: Number(e.target.value),
                  })
                }
              />
            </Row>
          </div>
          <p className="text-xs text-muted-foreground">
            Message fallback is always on: if a transfer fails, the receptionist takes a
            message. This cannot be disabled.
          </p>
        </Section>

        <Section title="Voice settings">
          <div className="grid gap-3 sm:grid-cols-2">
            <Row label="Voice ID">
              <Input
                value={working.voice.voice_id}
                onChange={(e) => update("voice", { ...working.voice, voice_id: e.target.value })}
              />
            </Row>
            <Row label="Speaking style">
              <Input
                value={working.voice.speaking_style}
                onChange={(e) =>
                  update("voice", { ...working.voice, speaking_style: e.target.value })
                }
              />
            </Row>
            <Row label="Filler phrases (comma-separated)">
              <Input
                value={working.voice.filler_phrases}
                onChange={(e) =>
                  update("voice", { ...working.voice, filler_phrases: e.target.value })
                }
              />
            </Row>
            <Row label="Maximum call duration (seconds)">
              <Input
                type="number"
                min={60}
                max={3600}
                value={working.voice.max_call_seconds}
                onChange={(e) =>
                  update("voice", { ...working.voice, max_call_seconds: Number(e.target.value) })
                }
              />
            </Row>
          </div>
        </Section>
      </fieldset>

      <Section title="Version history">
        {versions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No versions yet.</p>
        ) : (
          <ul className="space-y-2">
            {versions.map((version) => (
              <li
                key={version.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border p-3 text-sm"
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium">v{version.version}</span>
                  <Badge
                    variant={
                      version.state === "active"
                        ? "success"
                        : version.state === "rejected"
                          ? "danger"
                          : version.state === "pending_review"
                            ? "info"
                            : "neutral"
                    }
                  >
                    {version.state.replaceAll("_", " ")}
                  </Badge>
                  {version.review_notes && (
                    <span className="text-xs text-muted-foreground">
                      {version.review_notes}
                    </span>
                  )}
                </div>
                {version.state === "superseded" && (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={pending}
                    onClick={() => setRollbackTarget(version.version)}
                  >
                    Roll back to this
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <ConfirmDialog
        open={confirmingApprove}
        title="Approve and apply this configuration?"
        description="The approved configuration becomes live for the receptionist immediately. This action is audited."
        confirmLabel="Approve"
        pending={pending}
        onConfirm={() => run(() => approveAction(tenantId))}
        onCancel={() => setConfirmingApprove(false)}
      />
      <ConfirmDialog
        open={rollbackTarget !== null}
        title={`Roll back to version ${rollbackTarget}?`}
        description="A new active version is created from the older snapshot. The current configuration is superseded. This action is audited."
        confirmLabel="Roll back"
        destructive
        pending={pending}
        onConfirm={() => rollbackTarget !== null && run(() => rollbackAction(tenantId, rollbackTarget))}
        onCancel={() => setRollbackTarget(null)}
      />
    </div>
  );
}
