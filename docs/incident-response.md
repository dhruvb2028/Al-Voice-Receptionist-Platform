# Incident response

What to do when something breaks or leaks. Written to be followed at
3am by whoever is on call, which at this stage is one person.

## Severity

| Level | Meaning | Response |
|---|---|---|
| **SEV1** | Calls are not being answered, or tenant data was exposed | Immediately, drop everything |
| **SEV2** | Degraded: bookings failing, notifications not sending, a tenant broken | Within the hour |
| **SEV3** | Single-tenant annoyance, no data risk, workaround exists | Next working day |

When unsure between two levels, take the higher one. Downgrading later
is cheap; discovering you under-reacted is not.

## First five minutes

1. **Confirm it is real.** Check `/healthz` on api, voice, and worker,
   and the admin overview (`/admin/overview`) for live and failed calls.
2. **Declare it.** Note the start time and severity. Everything after
   this is recorded against that timestamp.
3. **Stop the bleeding before diagnosing.** A caller reaching a broken
   receptionist is worse than a caller reaching voicemail.
4. **Do not delete anything.** Logs, rows, and recordings are evidence
   until the incident is closed.

## Containment

| Situation | Action |
|---|---|
| One tenant is broken or under attack | Pause the tenant (`/admin/tenants/{id}/pause`) — inbound calls stop being answered |
| A staff account is compromised | Set the member's status away from `ACTIVE`; the next request is refused |
| A tenant is compromised or abusive | Set the tenant to `SUSPENDED`; every request is blocked at the auth dependency |
| A provider key leaked | Rotate in Secret Manager, redeploy; QStash accepts current *and* next signing keys, so job delivery survives rotation |
| A signed recording URL leaked | URLs expire in 15 minutes; if the object itself is exposed, delete it and set a legal hold on the call record to preserve the audit trail |
| The platform is answering wrongly | Roll back to the last green deploy — do not hot-patch a live voice path |

Suspension and revocation are **status changes, not deploys**. They take
effect on the next request.

## Diagnosis

Start from what the caller experienced, not from the logs.

1. Find the call in the admin console. The call detail shows outcome,
   failure category, per-turn latency, tool executions, and guardrail
   events.
2. Follow the request id. Every response carries `X-Request-ID`, and it
   is bound into every log line for that request.
3. Check the provider boundary. Provider errors are mapped to a fixed
   taxonomy (`timeout`, `unavailable`, `rate_limited`, `auth`,
   `bad_response`), so the log tells you which provider and which class.
4. Check whether post-call processing stalled — the platform overview
   lists tenants with stuck jobs.

## Suspected data exposure

Treat as SEV1 until disproven.

1. **Scope it.** Which tenants, which records, which time window. The
   audit log records every recording access with its actor, and
   notification deliveries record masked recipients.
2. **Preserve.** Snapshot the database and export relevant audit rows
   before any remediation changes state.
3. **Close the hole.** Revoke, rotate, suspend — in that order.
4. **Notify.** The operator tells the affected client with: what
   happened, what data, over what period, what has been done, and what
   the client should do. The client is the controller and notifies its
   own callers and regulator as its obligations require. Regulatory
   clocks are typically 72 hours from awareness — start the
   notification path on day one, not after the fix ships.
5. **Do not speculate** in writing about cause or blame before the
   review.

## Recovery

- Restore service before restoring elegance.
- Verify with a real call through the simulator, then a real phone call.
- Confirm the evaluation suite still passes (`python -m ai_evals.cli`) —
  exit code 2 means a safety property regressed and the fix is not
  finished.
- Re-enable any tenant paused during containment, and tell them.

## Backups and restore

- **Database.** Neon provides continuous backup with point-in-time
  restore. Restore to a new branch first, verify, then cut over —
  never restore in place over a live database.
- **Recordings.** R2 objects are the only copy once the provider copy is
  deleted. Deletion is retention-driven and audited; a recording deleted
  on schedule is not recoverable, which is the intent.
- **Configuration.** Tenant configuration is versioned in
  `config_versions` with rollback, so a bad configuration change is
  reversible without a database restore.
- **Code.** Every deploy is a tagged commit; rollback is redeploying the
  previous image.

Restore is only real if it has been rehearsed. Test a point-in-time
restore into a scratch branch before the first client goes live, and
again whenever the schema changes materially.

## After

Within a week, write a short review covering: what happened, the
timeline, what the caller or client experienced, why it happened
(mechanically — not "human error"), what stopped it being worse, and
what changes. Every action item gets an owner and a date, or it is not
an action item.

If a class of bug reached production, add an evaluation case for it. The
suite exists so the same failure cannot return quietly.
