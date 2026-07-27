# Monitoring and alerting

What is watched, where it is watched from, and what to do when something
fires.

## Three layers

| Layer | Answers | Where |
|---|---|---|
| **Error reporting** (Sentry) | What broke, with a stack trace | `ai_telemetry/sentry.py` |
| **Metrics** (in-process) | How fast, how many, per instance | `ai_telemetry/metrics.py` |
| **Health evaluation** (database) | Is the platform healthy *right now* | `api/services/health.py` |

Alerts are evaluated from **database state**, not from the in-process
registry. An operator asking "are calls failing?" needs an answer that
survives an instance restart and reads identically on every replica —
counters held in one process satisfy neither.

## Sentry

Configured per service by `configure_observability` at startup, with
environment separation and release tracking. A missing DSN is a no-op:
local runs and CI need no monitoring credentials, and monitoring must
never be the reason a service fails to start.

**Nothing personal is sent.** `scrub_event` runs as `before_send` and:

- drops the `user` context entirely
- redacts `Authorization`, `Cookie`, and provider signature headers
- drops request bodies, cookies, and query strings
- scrubs stack-frame local variables — the likeliest place a transcript
  leaks into a traceback
- scrubs `extra`, `contexts`, `tags`, and breadcrumb data

`send_default_pii` is off. The scrubber is deliberately blunt: an
over-scrubbed event is still actionable from its stack trace, an
under-scrubbed one is a privacy incident.

Dashboard errors are reported by the Next.js app's own Sentry
configuration with source maps uploaded at build time.

## Metrics catalog

Declared in `ai_telemetry/metrics.py`. Adding an alert means adding its
metric here first, so a threshold is always readable next to the thing
it measures.

`calls.active`, `calls.completed`, `calls.failed`, `calls.duration`,
`turn.response_latency`, `bookings.failed`, `transfers.failed`,
`providers.timeouts`, `worker.backlog`, `qstash.failures`,
`calendar.failures`, `database.latency`, `cache.latency`,
`providers.cost`, `recordings.upload_failed`.

Histograms keep a bounded reservoir. Eviction drops the **median**, not
an extreme — tail latency is what alerts fire on, so the tails are the
part worth keeping.

## Alerts

Every alert declares its threshold, window, and runbook beside the query
that evaluates it. All are visible on the admin **System health** page,
sorted so anything not OK appears first.

| Alert | Warn | Critical | Window |
|---|---|---|---|
| Call failures | 3 | 10 | 60m |
| p95 response latency | 2500 ms | 4000 ms | 60m |
| Booking failures | 2 | 5 | 60m |
| Calendar connections down | 1 | 3 | current |
| Transfers never connected | 2 | 5 | 60m |
| Provider errors | 5 | 15 | 60m |
| Concurrent-call saturation | 80% | 100% | 5m |
| Post-call backlog | 5 | 20 | 6h |
| Recording upload failures | 1 | 5 | 60m |
| Notification delivery failures | 3 | 10 | 60m |
| Database latency | 250 ms | 1000 ms | probe |
| Tenants with repeated failures | 1 | 3 | 60m |

Provider outages (Deepgram, Groq, Cartesia, Twilio) surface through
**provider errors** and **call failures**: adapters map vendor failures
onto a fixed category taxonomy, and the alert detail names the top
categories, so the failing provider is identifiable without a separate
alert per vendor.

Two properties every alert must have, enforced by test:

- **It is actually evaluated.** A declared alert that is never computed
  is a blind spot that looks like coverage.
- **It carries a runbook.** An alert with no next action wakes someone
  up for nothing.

## Reading the system-health page

`/admin/system-health` shows every alert with its current value, its
thresholds, and its window. Non-OK alerts show their runbook inline.
Tenants failing repeatedly are listed separately and link straight to
that tenant's failed calls — a single tenant failing usually means its
own configuration rather than a platform fault.

## Escalation

Severity maps to the incident levels in
[incident-response.md](incident-response.md): any **critical** alert is
at least SEV2, and critical saturation or call failures during business
hours is SEV1. Start there for containment steps.
