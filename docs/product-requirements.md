# Product Requirements Document

**Product:** Multi-tenant AI voice receptionist platform for home-services businesses
**Version:** 1.0 (first production release)
**Related:** [Architecture baseline](architecture/00-assessment.md)

This document defines what the first production version must do, for whom, and how we will know it works. Requirements use RFC-2119 language: **must** is binding for launch, **should** is expected but negotiable with a recorded reason, **may** is optional.

---

## 1. Users and roles

### 1.1 Platform administrator

Internal operator of the managed service. Not a client.

**Responsibilities**

- Create tenants and their authentication organizations
- Invite client owners
- Configure each tenant's receptionist: business info, services, prices, hours, service area, greeting, persona, voice, escalation rules
- Assign and unassign phone numbers
- Connect integrations (Google Calendar) on the tenant's behalf
- Run test calls against a tenant before activation
- Activate and pause tenants
- View platform health across all tenants
- Review failed calls and system errors
- Export monthly per-tenant reports
- Monitor usage and provider costs per tenant

**Access:** full read access across tenants through a dedicated admin surface; every cross-tenant action is audit-logged. Admin access **must not** be reachable from client-facing routes.

### 1.2 Client owner

The business owner or manager who pays for the service. One or more per tenant.

**Responsibilities**

- View the business's calls, with recordings and transcripts
- Review bookings created by the receptionist
- Review messages taken by the receptionist
- View usage (calls, minutes) for their business
- Request configuration changes (fulfilled by the platform administrator in v1)
- Manage staff access: invite, remove, and set staff permissions within their tenant

**Access:** full read access within their own tenant only. Owners **must not** be able to see or infer the existence of other tenants.

### 1.3 Client staff

Employees of the client business (dispatchers, office managers, technicians).

**Responsibilities**

- Review calls
- Review bookings
- Review messages
- Access only permitted operational information — staff **must not** see usage/billing data or manage other users

**Access:** read access within their tenant, reduced relative to the owner. Permission differences between owner and staff are enforced server-side.

### 1.4 Caller

The client business's customer. Interacts only by phone; has no account and no dashboard.

**Goals**

- Reach the business at its normal number
- Explain their issue in natural speech
- Receive appropriate assistance: an answer, a booking, or a message taken
- Book an appointment at a real available time
- Leave a message that reaches the business
- Reach a human when the situation requires it (emergency, explicit request, or the AI failing to help)

**Implicit requirements:** the caller never waits through long silences, is never given invented information, and always leaves the call with a defined outcome.

---

## 2. Product modules

### 2.1 Authentication

- The platform **must** use Clerk for identity, sessions, and organization membership.
- Each tenant **must** map to exactly one Clerk organization; role (owner/staff) derives from organization membership, platform-admin from a platform-level role.
- All dashboard routes and API endpoints **must** require an authenticated session; API requests **must** carry a short-lived JWT verified for signature, issuer, audience, and expiry.
- The tenant context of every request **must** be derived server-side from the authenticated organization — never from a client-supplied value.
- Sign-out **must** invalidate the session across dashboard and API.

### 2.2 Tenant management

- Platform admins **must** be able to create, configure, pause, and activate tenants.
- A tenant **must** have a lifecycle state: `draft → configuring → testing → active → paused`. Only `active` tenants receive live calls; `testing` tenants accept test calls only.
- Pausing a tenant **must** take effect for new calls immediately and **must not** drop calls already in progress.
- Tenant deletion in v1 is a soft archive: data retained per retention policy, numbers released, logins revoked.
- Every lifecycle transition **must** be audit-logged with actor and timestamp.

### 2.3 Business configuration

- Per tenant, the platform **must** store: business name, address, contact details; service catalog with per-service price or price range and duration; business hours with holidays/exceptions; service area (list of ZIP codes or radius); greeting text; AI persona parameters; selected TTS voice; escalation rules and transfer number; recording announcement toggle; retention period.
- All configuration writes **must** be validated (Zod on the dashboard, Pydantic at the API); invalid configuration **must** be rejected with field-level errors.
- Configuration changes **must** version: the previous value is retained in the audit trail.
- Changes **must** propagate to the live call path within 60 seconds without a deploy or restart.
- A tenant **must not** be activatable until required configuration is complete (greeting, at least one service, hours, escalation target).

### 2.4 Phone number mapping

- Each tenant **may** have up to 3 phone numbers; each number maps to exactly one tenant.
- The dialled number is the **sole** source of tenant identity for a call.
- Calls to unassigned or deactivated numbers **must** play a neutral unavailable message and end without exposing platform details.
- Number assignment and release **must** be admin-only and audit-logged.

### 2.5 Voice calls

- The receptionist **must** answer inbound calls within 2 rings under normal operation.
- Calls **must** flow: greeting (with recording announcement when configured) → conversation loop (streaming STT → LLM → streaming TTS) → outcome.
- The caller **must** be able to interrupt the receptionist mid-sentence (barge-in); playback stops and the caller's speech is processed.
- The platform **must** support at least 6 concurrent calls across tenants without degradation.
- Every call **must** end in exactly one recorded outcome: `booked`, `message_taken`, `transferred`, `answered_inquiry`, `caller_hangup`, or `failed`.

### 2.6 Conversation state

- The conversation **must** be governed by an explicit state machine (greeting, intent discovery, service qualification, scheduling, message taking, escalation, closing) — not free-running LLM output.
- The LLM context window per turn **must** be bounded; long calls summarize older turns rather than growing without limit.
- The state machine **must** track: current intent, collected entities (name, callback number, address, service, urgency), failed-resolution count, and escalation triggers.
- Two consecutive failures to resolve caller intent **must** route to escalation.

### 2.7 Appointment booking

- Booking **must** check real calendar availability before offering slots; offered slots **must** come only from the availability check.
- A booking **must** be committed as one transaction: database row (with idempotency key and overlap constraint) and calendar event; spoken confirmation **must** occur only after both succeed.
- On any failure mid-booking, the receptionist **must** offer an alternative slot or take a message — never confirm.
- Bookings **must** capture: caller name, callback number, service, address (validated against service area), time window, and notes.
- Callers outside the service area **must** be told so politely, with a message taken instead.

### 2.8 Message taking

- When the caller's need cannot be resolved (out-of-scope question, no availability, after-hours policy, failed booking), the receptionist **must** take a structured message: caller name, callback number, reason, urgency classification, free-text summary.
- Messages **must** appear in the dashboard within 60 seconds of call end and be included in notifications.
- A message **must** never be silently dropped; failure to persist a message is a call failure and alerts.

### 2.9 Escalations

- Escalation triggers, all mandatory: emergency classification (e.g., gas leak, burst pipe flooding, sparking panel), explicit request for a human, two failed intent resolutions, any unhandled voice-path error.
- Escalation **must** attempt a warm transfer to the tenant's configured number; if the transfer fails or goes unanswered, the receptionist **must** take a message and flag it urgent.
- Emergency escalations **must** be flagged on the call record and trigger an immediate notification.
- Escalation behaviour **must** be configurable per tenant only within safe bounds — the triggers themselves cannot be disabled.

### 2.10 Call records

- Every call **must** persist: tenant, number, caller ID (redacted per policy), start/end time, duration, outcome, escalation flag, per-turn transcript with speaker labels and timestamps, tool invocations with arguments and results, and per-stage latency metrics.
- Call records **must** be immutable after post-call processing completes.
- Call records **must** be visible in the dashboard within 60 seconds of call end.

### 2.11 Recordings

- When enabled, calls **must** be recorded; when the tenant configures an announcement, it **must** be played before conversation begins.
- Recordings **must** be stored in tenant-scoped R2 paths; the provider copy **must** be deleted after transfer.
- Recording playback **must** be available only to authenticated members of the owning tenant and platform admins; access **must** be via short-lived signed URLs, and admin access is audit-logged.
- Recordings **must** be deleted automatically at the end of the tenant's retention period.

### 2.12 Transcripts

- Every call **must** produce a turn-level transcript in near-real time, finalized by post-call processing.
- Transcripts **must** include speaker labels, timestamps, and tool-call markers.
- Transcripts **must** be searchable within a tenant by caller number, date range, outcome, and free text.
- Post-call processing **must** attach a short summary and outcome classification to each transcript.

### 2.13 Usage tracking

- Per tenant and per month, the platform **must** record: call count, total minutes, bookings created, messages taken, escalations, and estimated provider cost.
- Usage **must** be visible to platform admins per tenant and in aggregate; owners see their own tenant's usage.
- Usage data **must** be exportable (CSV) for manual invoicing.
- Approaching plan limits (calls/month) **should** raise an admin alert at 80% and 100%.

### 2.14 Client dashboard

- The client dashboard **must** include: overview (recent activity, key counts), calls list with detail view (transcript, recording, outcome), bookings list, messages list with urgent flagging, usage view (owner only), and staff management (owner only).
- All list views **must** paginate, filter by date range, and load within the responsiveness targets in §3.12.
- The dashboard **must** be usable by non-technical users: plain language, no developer terminology, no raw IDs or JSON.
- The dashboard **must** be responsive down to 375 px viewport width.

### 2.15 Platform-admin dashboard

- The admin dashboard **must** include: tenant list with lifecycle state and health, tenant creation and configuration flows, phone-number management, integration status per tenant, cross-tenant call failure review, usage and cost per tenant, and report export.
- Admin views **must** clearly display which tenant's data is on screen at all times.
- Admin actions that modify tenant state **must** require confirmation and are audit-logged.

### 2.16 Browser testing console

- The platform **must** provide an internal browser-based text simulator that runs the identical conversation engine (state machine, tools, guardrails) without telephony.
- Admins **must** be able to run a simulated conversation against any tenant's configuration, inspect state transitions, tool calls, and guardrail decisions per turn.
- The simulator **must** be clearly labelled non-production and **must not** create real bookings against live calendars unless explicitly toggled.
- Simulator sessions **should** be savable as regression scenarios for the evaluation harness.

### 2.17 Notifications

- The platform **must** notify tenants (email in v1) of: new bookings, new messages, and urgent/emergency escalations.
- Notification recipients and per-event toggles **must** be configurable per tenant.
- Urgent escalation notifications **must** dispatch within 60 seconds of the triggering event.
- Notification delivery failures **must** be retried with backoff and surfaced to admins after exhaustion.

### 2.18 Evaluation harness

- The platform **must** include an automated conversation evaluation suite runnable in CI and on demand.
- The suite **must** cover, at minimum: booking happy path, no-availability path, out-of-area caller, emergency escalation, explicit human request, out-of-scope question, price inquiry (exact answer from catalog only), and hallucination probes (attempts to elicit invented prices/services/hours).
- Each scenario **must** assert on outcome, tool usage, and guardrail behaviour — not on exact wording.
- A failing safety scenario (hallucination, wrong confirmation, missed escalation) **must** block deployment.

### 2.19 Monitoring

- All services **must** emit structured logs with request ID, call ID, and tenant ID; errors report to Sentry with release tags.
- The platform **must** track per-call, per-stage latency (STT, LLM, tools, TTS) and expose p50/p95 dashboards.
- Alerts **must** fire on: call failure rate, webhook verification failures, provider error rates, queue backlog, and database connection saturation.
- A synthetic health check **must** exercise each service's critical dependency path at least every 5 minutes.

### 2.20 Retention

- Recordings: per-tenant configurable retention, default 90 days; automatic deletion with a deletion audit event.
- Transcripts and call metadata: retained 24 months, then archived or deleted per tenant contract.
- Audit logs: retained a minimum of 24 months, append-only.
- A tenant leaving the platform **must** be able to receive an export of their calls, transcripts, bookings, and messages before archive.

### 2.21 Audit logs

- The platform **must** record append-only audit events for: configuration changes (with before/after), tenant lifecycle transitions, phone-number assignment, user invitations and role changes, recording access by admins, exports, and integration connections.
- Each event **must** capture actor, tenant, action, timestamp, and request ID.
- Audit logs **must** be visible to platform admins, filtered per tenant; owners **may** see their own tenant's configuration-change history.

---

## 3. Acceptance criteria

Each criterion is testable; those marked **CI** are enforced by automated tests that block deployment.

### 3.1 Tenant isolation — **CI**

- For every client-facing endpoint, an authenticated user of tenant A requesting a tenant-B resource by ID receives `404` (not `403`), with zero data in the body. Verified by an automated cross-tenant probe across all endpoints.
- Database queries issued by client-facing code paths carry a tenant scope; a query without one fails under row-level security in an integration test.
- No API response, log line, or client bundle contains another tenant's identifiers.

### 3.2 Booking correctness — **CI**

- 100% of confirmed bookings have a corresponding committed database row **and** calendar event.
- A forced failure injected between database write and calendar write results in rollback and no spoken confirmation, in an automated fault-injection test.
- Every booking row contains an idempotency key; replaying a booking request with the same key creates no duplicate.

### 3.3 Double-booking prevention — **CI**

- Two concurrent booking attempts for the same slot result in exactly one committed booking; the loser receives an alternative offer. Verified by a concurrency test executing simultaneous transactions.
- The database schema rejects overlapping bookings for the same tenant resource via constraint, demonstrated by a failing insert in tests.

### 3.4 Emergency escalation — **CI (evaluation harness)**

- 100% of evaluation-suite emergency scenarios (gas leak, flooding, electrical fire risk phrasing variants) end in `transferred` or, when transfer fails, an urgent message — never a booking flow continuation.
- Emergency calls display an emergency flag on the call record and dispatch a notification within 60 seconds.

### 3.5 Human-request escalation — **CI (evaluation harness)**

- 100% of evaluation scenarios containing an explicit human request ("let me talk to a person") route to transfer within one turn, regardless of conversation state.
- The receptionist never argues with or delays a human request.

### 3.6 Call persistence — **CI**

- Every answered call produces exactly one call record with all fields in §2.10; verified by an end-to-end test asserting record completeness.
- A crash of the voice service mid-call still yields a call record with `failed` outcome and partial transcript (verified by kill-test in staging).
- Call records appear in the dashboard within 60 seconds of call end (p95).

### 3.7 Call failure handling

- Any unhandled error during a call triggers transfer; if transfer fails, message capture; the caller never hears silence for more than 5 seconds or an abrupt disconnect from an application error.
- Provider outage (STT/LLM/TTS) mid-call degrades to transfer-or-message within 10 seconds.
- 100% of `failed` calls appear in the admin failure review queue with error context.

### 3.8 Webhook verification — **CI**

- Requests to Twilio/Clerk/QStash webhook endpoints without a valid signature receive `403` and produce no side effects; verified by automated tests with missing, malformed, and wrongly-signed payloads.
- Verification failures are logged with source IP and counted in monitoring; a spike alerts.

### 3.9 Dashboard authorization — **CI**

- Unauthenticated requests to any dashboard page or API endpoint redirect to sign-in / return `401`.
- Staff accounts receive `403`/hidden UI for owner-only surfaces (usage, staff management); verified per route in automated tests.
- Client accounts receive `404` on all admin routes; admin role checks execute server-side.

### 3.10 Recording access

- Recording URLs are signed and expire within 15 minutes; an expired or tampered URL returns `403`.
- A user of tenant A cannot retrieve a recording of tenant B by any URL manipulation (covered by the §3.1 probe).
- Every admin access of a client recording produces an audit event.

### 3.11 Configuration validation — **CI**

- Invalid configuration (empty greeting, service without price and without price-range, hours where close ≤ open, empty service area, malformed transfer number) is rejected with a field-level error and no partial write.
- A tenant missing required configuration cannot transition to `active`; the API refuses with an explicit reason list.
- Configuration changes reach the live call path within 60 seconds, verified by a staging test.

### 3.12 Browser responsiveness

- Dashboard initial page load (p75, staging, cold cache): ≤ 2.5 s LCP on desktop, ≤ 4 s on mid-tier mobile.
- List-view interactions (filter, paginate) render within 500 ms (p75) after data arrival; interaction to next paint ≤ 200 ms.
- All dashboard views function correctly at 375 px, 768 px, and 1440 px widths with no horizontal scroll.

### 3.13 Accessibility

- All dashboard pages pass automated axe checks with zero critical/serious violations — **CI**.
- Full keyboard operability: every interactive element reachable and operable without a mouse; visible focus states.
- Color contrast meets WCAG 2.1 AA; audio playback has transcript alternatives; motion respects `prefers-reduced-motion`.

### 3.14 Deployment readiness

- A commit to `main` deploys to staging via CI with zero manual steps; production deploys require one manual approval.
- Migrations run before service deploy and are backward-compatible with the previous release (verified by running old code against new schema in CI).
- Rollback to the previous revision completes within 10 minutes and is documented and rehearsed.
- All secrets load from Secret Manager; a scan of images and repository finds zero embedded secrets — **CI**.

### 3.15 Observability

- 100% of requests and calls carry a request/call ID traceable across services.
- Per-stage latency metrics exist for every completed call; dashboards show p50/p95 by tenant and stage.
- Every alert defined in §2.19 has a runbook entry; test alerts have been fired and received.
- Logs contain no unredacted caller phone numbers, addresses, or transcript bodies — verified by a log-scan test in staging.

---

## 4. Initial quality targets

> **These are design targets for the first production version, not measured achievements.** They become tracked SLOs once live traffic exists; the evaluation harness and latency telemetry measure progress against them from the first staging deploy.

| Metric | Target | Type |
|---|---|---|
| End-of-turn latency (caller stops speaking → receptionist audio starts), p50 | < 700 ms | Target |
| End-of-turn latency, p95 | < 1,100 ms | Target |
| Barge-in stop time (caller starts speaking → playback halts) | < 200 ms | Target |
| Booking task completion (caller intends to book and slot exists → booking succeeds) | > 90% | Target |
| Containment (calls resolved without human transfer, excluding mandatory escalations) | > 75% | Target |
| False escalation rate (transfers with no valid trigger) | < 10% | Target |
| Wrongly confirmed bookings (spoken confirmation without committed writes) | **0** | Hard requirement |
| Hallucinated prices, services, hours, or availability | **0** | Hard requirement |

The two hard requirements are enforced by design (§2.7, §2.18) and gate every deployment; the remaining targets are monitored and drive iteration priority after launch.

---

## 5. Out of scope for v1

The first production version deliberately excludes the following. Requests for any of these are recorded for the roadmap, not built ad hoc.

- Public self-service signup — tenants are created by the platform administrator
- Automated subscription billing — invoicing is manual; usage export supports it
- Automated phone-number purchasing — numbers are provisioned manually by the admin
- Multiple vertical-specific conversation engines — one engine, tuned for plumbing/HVAC/electrical
- Outbound sales or campaign calls — inbound only
- Voice cloning — voices come from the TTS provider's catalog
- Multilingual support — English only
- Native mobile apps — the responsive web dashboard is the mobile experience
- Enterprise SSO (SAML/OIDC federation) — Clerk-managed accounts only
- Dedicated per-tenant deployments — shared infrastructure with logical isolation
- Advanced CRM integrations (ServiceTitan, Jobber, HubSpot) — export is CSV/email
- White-labelling — the platform's own branding throughout
- Multi-region operation — one primary region

---

*Next: Prompt 2 — System Architecture.*
