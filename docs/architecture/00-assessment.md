# 00 — Repository Assessment & Proposed Architecture

**Scope:** Multi-tenant AI voice receptionist platform for home-services businesses (plumbing, HVAC, electrical).
**Status:** Pre-implementation. No application code exists yet. This document is the design baseline for everything that follows.

---

## 1. Repository assessment

### Current state

| Item | Finding |
|---|---|
| Version control | Not initialised. No `.git`, no remote, no branches. |
| Application code | None. |
| Package manifests | None (`package.json`, `pyproject.toml`, `requirements.txt` all absent). |
| Infrastructure as code | None. |
| CI/CD | None. |
| Tests | None. |
| Documentation | None except the build-sequence specification document at the repo root. |
| Secrets / config | No `.env`, no secret manager wiring. |

### Conclusion

This is a **greenfield build**. There is no existing implementation to preserve, no legacy schema to migrate, and no working code to reuse. Every architectural decision is open, which means the cost of getting the boundaries right now is low and the cost of getting them wrong is high — the domain model (tenants, calls, turns, bookings) must be correct before any voice code is written, because the call path will encode assumptions about it.

Two immediate housekeeping actions are required before any code lands:

1. Initialise git with a `.gitignore` that excludes the specification document, `.env*` files, build artefacts, and audio fixtures. The specification contains commercial terms that must not enter a public history.
2. Establish a `main` branch protected by CI, so the first commit already sits inside the quality gate rather than being retrofitted into it.

---

## 2. Proposed architecture

### 2.1 Shape

Four deployable units, one shared database, one shared cache. Not microservices — a **modular monolith split along a single hard latency boundary**.

The boundary that matters is: *is this code executing while a caller is waiting for audio?* Everything on that side is isolated so that dashboard traffic, report generation, and post-call processing can never contend with a live call for CPU, connection-pool slots, or event-loop time.

```
                        ┌───────────────────────┐
   Browser ────────────►│  web  (Next.js)       │  Clerk session
                        │  dashboard + admin    │
                        └───────────┬───────────┘
                                    │ server-side fetch (JWT)
                                    ▼
                        ┌───────────────────────┐
                        │  api  (FastAPI)       │◄──── Twilio webhooks (signed)
                        │  control plane        │◄──── Clerk webhooks (signed)
                        └───────────┬───────────┘
                                    │
   PSTN ──► Twilio ────────────────┐│
        Media Streams (WSS)        ││
                        ┌──────────▼▼───────────┐
                        │  voice (Pipecat)      │──► Deepgram / Groq / Cartesia
                        │  realtime call path   │
                        └───────────┬───────────┘
                                    │ QStash (call.ended)
                                    ▼
                        ┌───────────────────────┐
                        │  worker (FastAPI)     │──► R2, Resend, Google Calendar
                        │  post-call async      │
                        └───────────────────────┘

        Shared: Neon PostgreSQL (+pgvector) · Upstash Redis · Cloudflare R2
```

### 2.2 The four services

**`web` — Next.js dashboard.**
App Router, React Server Components for all data reads, server actions for mutations. Renders both the client dashboard and the platform-admin console; the two are separate route groups with separate layouts and separate authorisation checks, never a conditional inside a shared page. It holds no business logic — it is a rendering and session layer that calls `api`. This matters because it means the dashboard can never become a second, divergent implementation of tenant authorisation.

**`api` — FastAPI control plane.**
Owns the domain: tenants, users, business configuration, services and prices, hours, service areas, escalation rules, calls, bookings, messages, usage, audit. Owns all migrations. Receives and verifies provider webhooks. This is the only service that writes configuration.

**`voice` — Pipecat realtime orchestrator.**
Holds one WebSocket per active call plus upstream sockets to Deepgram and Cartesia. Runs the conversation state machine and calls tools. Deliberately does **not** own configuration writes; it reads a tenant's resolved receptionist config from Redis (written by `api` on change, TTL-backed with a Postgres fallback read) so a call never blocks on a cold config query.

**`worker` — post-call processor.**
Invoked by QStash over authenticated HTTP after a call ends. Produces the transcript artefact, summary, outcome classification, quality flags; moves the recording into R2; sends notifications; writes usage rows. Async, retryable, idempotent — nothing here is allowed to be on the critical path of a call.

### 2.3 Why this split and not another

- **A single service would be wrong.** A long-running Pipecat process holding stateful WebSockets has completely different scaling, deployment, and failure characteristics from a stateless REST API. Deploying a dashboard bugfix must not drop six live calls.
- **Splitting further would also be wrong.** At 10 tenants and 6 concurrent calls, separate services for bookings, telephony, and configuration would buy nothing and cost distributed-transaction complexity on the one operation that most needs atomicity: booking an appointment.
- **`worker` is separate rather than a thread in `api`** because post-call LLM summarisation is slow and bursty, and QStash gives retries, backoff, and dead-lettering for free.

### 2.4 Deferred by design

Not built in v1, and the architecture should not be contorted to anticipate them: public self-registration, automated subscription billing, multi-region, non-English, outbound campaigns, per-tenant custom models. `BillingProvider` exists as an interface with a manual/no-op implementation so that adding Stripe later is an adapter, not a refactor.

---

## 3. Service boundaries

| Concern | Owner | Everyone else |
|---|---|---|
| Schema & migrations | `api` | read via repositories only |
| Tenant/user/config writes | `api` | — |
| Twilio webhook verification | `api` | — |
| Live media, STT, LLM, TTS | `voice` | — |
| Conversation state machine | `voice` | — |
| Tool execution during a call | `voice` (calls `api` domain services in-process via shared package) | — |
| Booking write + calendar write | shared domain package, executed by `voice` inside one transaction | — |
| Transcript, summary, outcome | `worker` | — |
| Recording storage lifecycle | `worker` | — |
| Notifications | `worker` | `api` for invite emails only |
| Session & identity | Clerk, verified in `web` and `api` | `voice` uses call-derived identity, never a browser token |

**Shared code lives in a versioned internal package, not copy-paste.** A Python package (`packages/core`) containing domain models, provider interfaces, repositories, and the booking transaction is imported by `api`, `voice`, and `worker`. A TypeScript package (`packages/contracts`) containing Zod schemas generated from the FastAPI OpenAPI spec is imported by `web`. Drift between the dashboard's idea of a `Booking` and the API's idea of a `Booking` is a class of bug worth eliminating structurally.

---

## 4. Data-flow overview

### 4.1 Inbound call (the critical path)

1. Caller dials a tenant's number. Twilio POSTs to `api` `/webhooks/twilio/voice`.
2. `api` verifies `X-Twilio-Signature` against the raw body. Unsigned or mismatched → `403`, logged, no side effects.
3. **Tenant resolution by destination number only.** `To` → `phone_numbers` lookup → `tenant_id`. If the number is unknown or its tenant is not `active`, return a neutral TwiML message and end. No tenant identifier is ever accepted from the caller or the browser.
4. `api` creates a `calls` row (`status=ringing`), mints a short-lived signed `call_token`, and returns TwiML: optional recording announcement, then `<Connect><Stream url="wss://voice.../ws?token=...">`.
5. Twilio opens the media WebSocket to `voice`. `voice` validates the token, loads resolved tenant config from Redis, and starts the Pipecat pipeline.
6. Loop, per turn: inbound µ-law frames → Deepgram streaming STT → endpointing → state machine assembles a bounded context window → Groq → either speech or a tool call → Cartesia streaming TTS → frames back to Twilio. Caller speech during playback triggers barge-in: TTS is cancelled and the buffer flushed.
7. Tools resolve against tenant data only — services, prices, hours, service area, calendar availability. A tool that cannot find an answer returns "unknown", and the model is constrained to say it does not know rather than invent one.
8. Booking is a single transaction: check calendar availability → insert `bookings` row with a unique constraint on `(tenant_id, idempotency_key)` and an overlap guard → write the calendar event → commit. **Confirmation is spoken only after commit.** Any failure rolls back and the conversation offers alternatives or takes a message.
9. Escalation triggers — emergency classification, explicit human request, two failed intent resolutions, unhandled error — attempt a warm transfer. If transfer fails, the fallback is always: take a message, persist it, notify.
10. Turn-level records (text, latency per stage, tool calls, errors) are written asynchronously so persistence never stalls audio.
11. On hangup, `voice` finalises the `calls` row and publishes `call.ended` to QStash.

### 4.2 Post-call

QStash → `worker`: assemble transcript → summarise and classify outcome → compute quality flags (dead air, repeated escalation, tool failures) → fetch the Twilio recording, write it to R2 under a tenant-scoped key, delete the provider copy → write usage → dispatch notifications → mark processed. Idempotent on `call_id`; QStash retries are safe.

### 4.3 Dashboard read

Browser → `web` (RSC) → Clerk session → active organisation → `tenant_id` derived server-side → `api` with a forwarded JWT → repository layer applies `tenant_id` to every query → response. The client bundle never receives a tenant identifier it could tamper with, because it never sends one.

---

## 5. Security boundaries

Five trust boundaries, each with an explicit control:

1. **Browser → `web`.** Clerk session cookie. All authorisation server-side. No tenant identifier accepted from the client; it is derived from the authenticated Clerk organisation on every request.
2. **`web` → `api`.** Short-lived Clerk JWT with audience and issuer validation. `api` re-derives `tenant_id` from the token's organisation claim and ignores any tenant field in the request body. The dashboard is untrusted input like any other client.
3. **Provider → `api`.** Twilio signature verification on the raw body; Clerk webhook signature verification. Replay windows enforced. Failures are audited.
4. **`voice` ↔ Twilio media.** Single-use, short-TTL signed token bound to the `call_sid` and `tenant_id`, issued at step 4 and validated at socket open. A leaked URL is useless after the call.
5. **Application → database.** Two independent layers: tenant-scoped repositories that structurally cannot emit an unscoped query, *and* PostgreSQL row-level security keyed on a per-transaction `app.tenant_id`. Either alone is a single point of failure; together, a repository bug is contained by the database and a policy gap is contained by the code.

**Admin plane separation.** Platform-admin capability is a Clerk role checked in `api` on a dedicated `/admin` router with its own dependency chain and its own audit stream. Client-facing endpoints have no code path that can return cross-tenant data, so an admin bug cannot leak through a client route.

**Secrets.** Google Secret Manager, injected at deploy. Nothing in the repository, nothing in the image. Local development uses `.env.local`, git-ignored, populated from a documented template.

**Data handling.** Structured logs redact phone numbers, caller names, addresses, and transcript bodies at the formatter level rather than at each call site. Recordings carry a per-tenant retention period enforced by a scheduled deletion job. Audit rows are append-only and cover configuration changes, phone-number assignment, tenant activation, role changes, recording access, and export.

---

## 6. Cloud deployment architecture

| Component | Platform | Notes |
|---|---|---|
| Marketing site | Framer | Independent of the app; links to the dashboard. |
| `web` | Cloud Run (container) | Next.js standalone output. Min instances 0. |
| `api` | Cloud Run | Min instances 1 to keep webhook latency predictable. |
| `voice` | Cloud Run | Min instances 1, **CPU always allocated**, session affinity on, generous request timeout for long WebSockets, concurrency tuned to ~6 calls per instance. |
| `worker` | Cloud Run | Min instances 0, scale-to-zero; woken by QStash. |
| Images | Google Artifact Registry | Built and pushed by GitHub Actions. |
| Database | Neon PostgreSQL + pgvector | Pooled connection string for `web`/`api`; direct for migrations. Branch per preview environment. |
| Cache / live state | Upstash Redis | Resolved tenant config, live-call state, rate limits, idempotency keys. |
| Queue | Upstash QStash | `call.ended`, notifications, retention sweeps. Signed delivery. |
| Object storage | Cloudflare R2 | Recordings and transcript artefacts, tenant-prefixed keys, no public access. |
| Errors / tracing | Sentry | All four services, release-tagged. |
| CI/CD | GitHub Actions | Lint → typecheck → test → build → push → migrate → deploy. |

**Environments.** `dev` (Neon branch, test provider credentials), `staging` (production-shaped, Twilio test numbers, full deploy on merge to `main`), `production` (manual approval gate, real numbers). Every feature must be exercisable in a hosted environment — nothing depends on a laptop, tunnel, or local Docker daemon.

**Deployment safety.** Migrations run as a separate CI job before the service deploy and must be backward-compatible with the currently-running revision, so a rollback never lands on an incompatible schema. `voice` deploys drain gracefully: new revisions take new calls, the old revision finishes the calls it holds.

---

## 7. Environment-variable inventory

Grouped by owning service. Every entry is a secret unless marked *public*.

**Shared**
`ENVIRONMENT` *(public)*, `LOG_LEVEL` *(public)*, `SENTRY_DSN`, `SENTRY_RELEASE` *(public)*

**Database**
`DATABASE_URL` (pooled), `DATABASE_DIRECT_URL` (migrations)

**Cache & queue**
`UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`, `QSTASH_TOKEN`, `QSTASH_CURRENT_SIGNING_KEY`, `QSTASH_NEXT_SIGNING_KEY`

**Auth (Clerk)**
`CLERK_SECRET_KEY`, `CLERK_WEBHOOK_SECRET`, `CLERK_JWT_ISSUER` *(public)*, `CLERK_JWT_AUDIENCE` *(public)*, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` *(public)*

**Telephony**
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WEBHOOK_BASE_URL` *(public)*

**Speech & language**
`DEEPGRAM_API_KEY`, `GROQ_API_KEY`, `GROQ_MODEL` *(public)*, `CARTESIA_API_KEY`

**Calendar**
`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` *(public)*

**Storage**
`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` *(public)*, `R2_ENDPOINT` *(public)*

**Email**
`RESEND_API_KEY`, `EMAIL_FROM` *(public)*

**Service-to-service**
`API_BASE_URL` *(public)*, `VOICE_WS_BASE_URL` *(public)*, `WORKER_BASE_URL` *(public)*, `CALL_TOKEN_SIGNING_KEY`, `INTERNAL_SERVICE_TOKEN`

**Frontend**
`NEXT_PUBLIC_APP_URL` *(public)*, `NEXT_PUBLIC_SENTRY_DSN` *(public)*

Startup validation: each service parses its own environment through a Pydantic (or Zod) settings model and **fails fast** on a missing or malformed value rather than discovering it mid-call.

---

## 8. Implementation order

Sequenced so that each phase produces something testable and nothing is built on an unverified foundation.

| Phase | Prompts | Outcome |
|---|---|---|
| **A — Contract** | 1–2 | Requirements with acceptance criteria; architecture ratified. Nothing shippable, everything decidable. |
| **B — Foundation** | 3–5 | Monorepo, tooling, CI skeleton, cloud accounts provisioned, database schema and first migration. The schema is the highest-leverage artefact in the build. |
| **C — Control plane** | 6–9 | Clerk auth and tenant authorisation; admin tenant management; dashboard shell; business configuration. First point at which a tenant can be created and configured end to end. |
| **D — Conversation, without telephony** | 10–14 | Provider interfaces; browser text simulator; state machine; live LLM; business tools. **Deliberately ahead of Twilio** — the conversation logic gets iterated in a fast text loop instead of by placing phone calls. |
| **E — Real-world tools** | 15–16 | Google Calendar; guardrails. Booking becomes real and safe. |
| **F — Voice path** | 17–19 | Twilio inbound; Deepgram STT and endpointing; Cartesia TTS and barge-in. First live phone call. |
| **G — Durability** | 20–22 | Call persistence and telemetry; recordings to R2; post-call worker. |
| **H — Client-facing surfaces** | 23–26 | Calls, bookings, messages, overview and usage dashboards; notifications. |
| **I — Confidence** | 27–29 | Evaluation harness; security hardening; monitoring and alerting. |
| **J — Launch** | 30–35 | Production deploy; marketing site; onboarding workflow; readiness review; first client; documentation. |

The one ordering decision worth naming: **Phase D precedes Phase F.** Building the conversation over a text simulator before wiring telephony means the state machine, prompt, tools, and escalation rules are debugged without audio latency, phone bills, or Twilio in the loop — and the text simulator survives as a permanent regression-testing surface.

---

## 9. Primary technical risks

**1. End-to-end latency.** Four network hops per turn (STT → LLM → tools → TTS) against a target of roughly 800 ms perceived response. *Mitigation:* streaming everywhere, no buffering between stages; Groq chosen specifically for inference speed; tenant config pre-resolved in Redis; tool calls bounded by hard timeouts with a spoken filler if a tool exceeds ~400 ms; per-stage latency recorded on every turn from day one so regressions are visible rather than anecdotal.

**2. Hallucinated business facts.** The single most damaging failure mode — an invented price or a promised slot creates real liability. *Mitigation:* the model is never given latitude to answer from parametric knowledge. Prices, services, hours, and areas come only from tool returns; tools return explicit "unknown"; the system prompt and a post-generation guardrail both enforce deferral; the evaluation harness (Phase I) tests this adversarially before launch.

**3. Double-booking.** Two callers, one slot, a race between calendar check and calendar write. *Mitigation:* database-level exclusion/unique constraints as the source of truth rather than an application-level check; mandatory idempotency keys; confirmation strictly after commit; the calendar is treated as a replica of the booking table, not the authority.

**4. Cross-tenant data exposure.** The failure that ends the business. *Mitigation:* the defence-in-depth pair in §5 (scoped repositories plus RLS), tenant resolution from the dialled number only, and an automated test that asserts every client-facing endpoint returns 404 for another tenant's resource — enforced in CI, not by review.

**5. Stateful WebSockets on a request-scaled platform.** Cloud Run is optimised for short requests; a 12-minute call is not one. *Mitigation:* CPU always allocated, session affinity, long request timeout, conservative concurrency, graceful drain on deploy. *Escalation path:* if Cloud Run proves unsuitable under load testing, `voice` moves to a platform with first-class long-lived connections — this is why `voice` is a separate deployable, and why the move would be a deployment change rather than a rewrite.

**6. Provider outage.** Deepgram, Groq, Cartesia, or Twilio going down takes calls with it. *Mitigation:* every provider sits behind an interface with bounded retries and circuit breaking; a degraded mode transfers to a human or takes a message rather than failing the call silently; alerting fires on provider error rate, not just on service errors.

**7. Cost drift.** Per-minute STT/LLM/TTS costs are invisible until the invoice arrives. *Mitigation:* usage recorded per call and per tenant from Phase G, surfaced in the usage dashboard, with per-tenant monthly ceilings and alerting well before the platform limits are reached.

**8. Operational load of a managed service.** Manual onboarding at 10 tenants is fine; it is also the thing that quietly consumes all available time. *Mitigation:* the onboarding workflow (Prompt 32) is treated as a product surface with a checklist and automated verification, not as tribal knowledge.

---

## 10. Decisions requiring confirmation

1. **Dashboard hosting.** Cloud Run is proposed for `web` to keep one deployment pipeline and one cloud vendor, consistent with the specified stack. Vercel would give better Next.js ergonomics and preview deployments at the cost of a second platform. *Recommendation: Cloud Run.*
2. **Recording retention default.** Proposed 90 days, per-tenant overridable, with a legal-hold flag. Needs a business answer, not a technical one.
3. **Transfer target.** Whether escalation dials a tenant-configured number, a hunt group, or the caller's existing number needs to be pinned down before Prompt 16.

---

*Next: Prompt 1 — Product Requirements and Acceptance Criteria.*
