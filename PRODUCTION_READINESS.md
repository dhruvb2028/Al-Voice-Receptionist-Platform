# Production readiness

An audit of what is actually built, tested, and safe to run — and what is
not. Written to be useful rather than reassuring.

**Rule applied throughout:** a capability is called production-ready only
if it is implemented *and* covered by a test that would fail if it broke.
Everything implemented but unverified against a real provider is listed
separately, because the difference is the whole point of this document.

Audited at commit `9449af2`. Re-run the validation set below before
trusting these figures again.

---

## Verdict

**Not yet ready for a live customer.** Not because of a defect, but
because no part of the realtime voice path has ever run against real
providers or a real phone call. The software is complete and internally
verified; it is unproven.

The gap is credentials and one supervised test call, not code.

---

## Measured now

| | |
|---|---|
| Backend tests | **599 passing** |
| Evaluation cases | **64 passing, 0 safety failures** (32 required scenarios covered) |
| Type checking | mypy strict, **0 errors** across 150 source files |
| Linting | ruff, **clean** |
| Frontend | lint clean, typecheck clean, production build succeeds |
| Migrations | **10**, single head, applies to an empty database |
| Migration reversibility | every migration reverses or declares `IRREVERSIBLE:` with a reason |

These are counts of *our own* checks. They say the code does what we
told it to; they say nothing about how it behaves against Twilio audio.

---

## Verified functionality

Implemented and covered by tests that would catch a regression.

### Security and isolation
- Clerk JWT verification (issuer, audience, JWKS, expiry)
- Tenant isolation, two independent layers: application scoping and
  Postgres RLS; cross-tenant ids return 404, not 403
- Admin routes invisible (404) to client principals
- Suspended tenants and non-active members refused at the dependency
- Twilio webhook HMAC-SHA1 verification; forged signature rejected
- QStash JWT verification with URL and body-hash claims, current→next
  key rotation
- Media WebSocket single-use signed tokens; replay refused
- Recording access only via short-lived signed URLs, audited per mint
- Request-size cap, per-principal rate limit, security headers
- Log redaction and Sentry scrubbing of personal and conversation data

### Conversation
- 22-state deterministic machine with a guarded transition table
- Guardrail pipeline: price, service, and availability invention
  firewalls; booking-confirmation gate
- Six business tools with a closed schema
- Prompt injection refused (safety-critical evaluation cases)
- Endpointing engine with injectable clock; barge-in with heard-portion
  estimation

### Data
- Booking idempotency, including a **real-database concurrency race**:
  four simultaneous transactions on one key produce exactly one booking
- Message capture with encryption at rest
- Post-call extraction: idempotent, dead-letters on malformed output,
  never overwrites a confirmed booking
- Recording lifecycle: ingest, retention sweep with legal hold, audited
  deletion
- Notification dispatch: per-tenant channels, jurisdiction-aware SMS
  consent, duplicate prevention, suppression recorded rather than thrown

### Surfaces
- Client dashboard: calls, bookings, messages, overview, usage
- Admin console: tenants, configuration, calls, overview, system health,
  onboarding with generated reports
- Metrics computed from persisted rows; unmeasured values stay null

---

## Implemented but unverified against reality

Working in tests with mocked providers. **None has processed a real
phone call.** This is the honest centre of this document.

| Area | State | What is missing |
|---|---|---|
| Deepgram STT | Adapter + parse tests | Never fed live audio |
| Cartesia TTS | Adapter + tests | Never produced audible speech |
| Groq LLM | SSE streaming + retry tested | Never run under real latency |
| Twilio media streams | Framing tested | Never carried a real call |
| Barge-in | Logic tested | Never interrupted a real human |
| End-to-end latency | Simulated only | **No real p50/p95 exists** |
| Google Calendar | OAuth + adapter tested | Never wrote to a real calendar |
| Resend / Twilio SMS | Adapters tested | Never delivered a real message |
| R2 storage | boto3 wrapper tested | Never stored real audio |
| Cloud Run | Manifests written | Never deployed |

Any latency figure quoted before a real call is a guess. The overview
dashboard will show real numbers once calls exist; until then it
correctly shows nothing.

---

## Launch blockers

Must be resolved before a customer's number points here.

1. **No production credentials.** Twilio, Deepgram, Cartesia, Groq,
   Google OAuth, Resend, R2, Clerk, Neon, QStash, Sentry.
2. **Never deployed.** The Cloud Run manifests have not run.
3. **No real call has ever completed.** The single most important gap.
4. **Backup restore never rehearsed.** An untested restore is not a
   backup ([backup-restore.md](docs/backup-restore.md)).
5. **No legal review.** Privacy policy and terms are drafted from the
   implementation, not reviewed by counsel — and this product records
   phone calls.
6. **Pricing undecided**, so the marketing site cannot launch.

---

## Known limitations

Accepted for v1, documented so nobody discovers them at 3am.

- **Six concurrent calls, platform-wide.** 2 per instance × 3 instances.
  Caller seven hears a courteous unavailable message. Adequate for the
  first clients; raise `maxScale` before the fifth tenant.
- **Rate limiting is per instance.** In-process counters, so the real
  ceiling is the limit times the instance count. Correct at this scale,
  wrong at ten instances — replace with a Redis-backed limiter then.
- **Encryption key rotation is not supported.** Ciphertext is versioned
  so it *can* be added, but no re-encryption migration exists. Rotating
  the data key today would make every encrypted column permanently
  unreadable.
- **Evaluation cases are scripted, not generated.** They pin behaviour
  deterministically and run without provider keys, which is what makes
  them useful in CI — but they cannot discover a failure mode nobody
  thought of.
- **English only.**
- **Metrics retention is process-lifetime.** Alerting reads the database
  instead, so this does not affect correctness.
- **No automated frontend tests.** The dashboard is covered by
  typechecking and build only; its behaviour is verified through the API
  tests behind it.

---

## Required credentials

| Service | Variables |
|---|---|
| Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_SMS_FROM_NUMBER` |
| Deepgram | `DEEPGRAM_API_KEY` |
| Cartesia | `CARTESIA_API_KEY` |
| Groq | `GROQ_API_KEY` |
| Google | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` |
| Resend | `RESEND_API_KEY`, `RESEND_FROM_ADDRESS` |
| Cloudflare R2 | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` |
| Upstash | `QSTASH_TOKEN`, `QSTASH_CURRENT_SIGNING_KEY`, `QSTASH_NEXT_SIGNING_KEY` |
| Clerk | `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` |
| Neon | `DATABASE_URL`, `DATABASE_DIRECT_URL` |
| Sentry | `SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_DSN`, `SENTRY_AUTH_TOKEN` |
| Platform | `DATA_ENCRYPTION_KEY`, `LOOKUP_HASH_KEY`, `CALL_TOKEN_SIGNING_KEY` |

The last row is generated by us, must be independent 32-byte values, and
**must be backed up outside the database** — losing them is unrecoverable.

## Manual configuration

Not automated, by design — each involves a decision or an external
account:

1. GCP projects, Workload Identity Federation, Artifact Registry
2. Neon project and branches
3. Twilio number purchase and webhook URL
4. Google OAuth consent screen (verification takes days — start early)
5. Clerk application and organisation setup
6. R2 bucket, private
7. DNS for the dashboard and marketing site
8. Per-tenant: services, prices, hours, service area, greeting,
   escalation number

---

## Validation set

Run all of it before any production change.

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy packages services && uv run pytest -q
```

```bash
uv run python -m ai_evals.cli --require-coverage
```

```bash
bash infra/scripts/migrate.sh verify
```

```bash
cd apps/dashboard && npm run lint && npx tsc --noEmit && npm run build
```

Migration-from-empty and the seed flow are verified by creating a fresh
database and running `alembic upgrade head` followed by
`ai_database.seed`. Both pass as of this audit.

Exit codes matter: the evaluation suite returns **2** for a safety
regression and **1** for a quality failure, so CI can gate on safety
separately.

---

## Staging test plan

Full checklist: [staging-checklist.md](docs/staging-checklist.md).
Shape of it:

1. Deploy; confirm all three services healthy.
2. Nine scripted conversations in the text simulator, including the
   refusal cases (out of area, unsupported service, discount pressure,
   prompt injection).
3. One real phone call: greeting, barge-in, booking, clean hangup.
4. Verify the post-call record: transcript, summary, notification with
   **no transcript in the body**, usage recorded.
5. Verify isolation: cross-tenant 404, client 404 on `/admin/*`.
6. Exercise failure paths deliberately: calendar down, transfer
   unanswered, forged webhook, oversized request.
7. Confirm Sentry received events with no personal data in them.

---

## Client-one launch plan

Ordered so the riskiest unknown is resolved earliest.

**Week 1 — infrastructure.** Provision GCP, Neon, and every provider
account. Deploy to staging. Rehearse a backup restore. *Exit: staging
answers a real call.*

**Week 2 — prove the voice path.** Fifty test calls against staging
across the scenario catalog. Measure real p50/p95 latency and record it
here, replacing the guesses. Tune endpointing against real speech.
*Exit: latency measured and acceptable; no safety failure in fifty
calls.*

**Week 3 — configure client one.** Work the onboarding checklist
(`/admin/tenants/{id}/onboarding`). Real services, prices, hours,
service area. Client approves the greeting. Connect their calendar.
Verify the escalation number rings a human. *Exit: every onboarding
blocker cleared, or waived with a written reason.*

**Week 4 — supervised launch.** Point the number during business hours
with someone watching the system-health page. Start with call forwarding
on unanswered only, so the platform takes overflow rather than
everything. Review every call for the first three days. *Exit: a week
with no safety failure and no missed emergency.*

**Then widen.** Full-time answering, then a second tenant — which is
configuration, not code.

### Stop conditions

Pull the number immediately if any of these occur:

- An emergency was not escalated
- A price the client never approved was quoted
- A booking was confirmed that did not exist
- Caller data appeared in a log, an email, or Sentry
- Two consecutive calls failed for the same reason

Each is a SEV1. Pause the tenant, then follow
[incident-response.md](docs/incident-response.md).
