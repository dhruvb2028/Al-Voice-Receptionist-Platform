# Production checklist

Two parts: the one-time gate before the platform ever answers a real
customer's call, and the short check run after every production deploy.

---

## Part 1 — Before the first live call

Once. Everything here must be true before a real customer can reach the
receptionist.

### Infrastructure

- [ ] Production GCP project separate from staging
- [ ] Production Neon database separate from staging, with point-in-time
      restore enabled
- [ ] A point-in-time restore has been **rehearsed** into a scratch
      branch — an untested restore is not a backup
- [ ] R2 bucket created, private, with no public access policy
- [ ] Voice service `minInstances: 1` (a cold start is a caller hearing
      silence)
- [ ] Concurrency cap set and understood: 2 per instance × 3 instances =
      6 concurrent calls

### Secrets

- [ ] Every provider key is a **production** key
- [ ] All secrets in Secret Manager, none in the repository or CI logs
- [ ] `DATA_ENCRYPTION_KEY` and `LOOKUP_HASH_KEY` are independent 32-byte
      keys, generated for production only
- [ ] `CALL_TOKEN_SIGNING_KEY` set and unique to production
- [ ] Key material backed up somewhere recoverable — losing the
      encryption key means losing every encrypted field permanently

### Telephony

- [ ] Production number provisioned and pointed at the production
      webhook URL
- [ ] Twilio signature verification confirmed working (a forged request
      returns 401)
- [ ] Escalation number verified: it rings a human who answers
- [ ] SMS consent recorded for any number that will receive texts

### Compliance

- [ ] Recording enabled only where the client wants it, with their
      approved consent notice
- [ ] Retention window agreed with the client and configured
- [ ] Client has read [privacy.md](privacy.md) and added the platform to
      their own privacy notice
- [ ] SMS rules confirmed for the client's jurisdiction — the platform
      defaults to the strictest policy, which may be stricter than needed

### Configuration

- [ ] Tenant configuration reviewed and approved (`approved_at` set)
- [ ] Services, prices, and hours match what the business actually offers
- [ ] Every customer-visible price is marked approved — the receptionist
      cannot quote anything else
- [ ] Service area matches reality
- [ ] Greeting reviewed and spoken aloud by a human before going live

### Operations

- [ ] Sentry receiving production events with `environment=production`
- [ ] System-health page reachable and reading correctly
- [ ] On-call person identified, with [incident-response.md](incident-response.md) read
- [ ] Rollback rehearsed at least once in staging

---

## Part 2 — After every production deploy

Five minutes.

- [ ] Migration gate succeeded
- [ ] All three services healthy (`/healthz`, `/readyz`)
- [ ] No spike in Sentry since the deploy
- [ ] System-health page shows no new firing alerts
- [ ] One real phone call: answers, converses, books, ends cleanly
- [ ] That call appears in the dashboard with a correct transcript and
      summary
- [ ] Notification for that call arrived
- [ ] Active calls draining from the previous revision have completed
      before deleting it

If any check fails, roll back rather than investigate: see
[rollback.md](rollback.md).

## Deploying while calls are live

Cloud Run drains rather than cuts, so in-flight calls finish on the old
revision. It is still worth deploying during a quiet window — check the
system-health page's active-call count first.
