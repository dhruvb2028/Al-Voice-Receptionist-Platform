# Rollback

Getting back to a known-good state. Read this before you need it.

## The decision

Roll back when service is degraded and the cause is not obvious within a
few minutes. **Do not debug in production while calls are failing.**
Restore first, diagnose after — the evidence is in the logs and the
database either way.

Roll forward instead only when the fix is genuinely trivial and
understood, and the previous release is also broken.

## Code rollback

Cloud Run keeps previous revisions, so a rollback is a traffic switch
rather than a rebuild:

```bash
gcloud run revisions list --service receptionist-api-production --region "$REGION"
```

```bash
gcloud run services update-traffic receptionist-api-production --to-revisions <REVISION>=100 --region "$REGION"
```

Roll back the service that is failing. They deploy independently and
their contracts are versioned, so rolling back only the API is normal.

**Voice needs care.** Switching traffic does not end calls already in
progress — the old revision drains while the new one takes new calls.
That is the desired behaviour: a caller mid-sentence is not disconnected.
Wait for the drain before deleting a revision.

## Dashboard rollback

Cloudflare Pages keeps every deployment. Promote the previous one from
the Pages dashboard, or redeploy the prior commit. The dashboard is
stateless, so this is always safe.

## Database rollback

This is the part that needs thought, because a schema change is not a
traffic switch.

### If the migration is reversible

```bash
infra/scripts/migrate.sh status "$DATABASE_DIRECT_URL"
```

```bash
DATABASE_DIRECT_URL="$URL" uv run alembic downgrade -1
```

Downgrade **after** the code that depends on the new schema is rolled
back, not before. The reverse order gives you a live service querying
columns that no longer exist.

### If the migration is irreversible

Some are, and they say so: a migration marked `IRREVERSIBLE:` cannot be
downgraded (PostgreSQL cannot drop enum values, for instance). Options,
in order of preference:

1. **Roll back the code only.** Additive migrations — a new nullable
   column, a new enum value — are usually harmless to leave in place. The
   old code ignores what it does not know about. This is why additive
   changes are preferred.
2. **Write a forward fix.** A new migration that undoes the effect, even
   if it cannot undo the structure.
3. **Point-in-time restore.** Last resort, because it loses everything
   written since the restore point — real bookings and real messages.
   See [backup-restore.md](backup-restore.md).

### Data written by the bad release

Rolling back code does not un-write rows. Check what the bad release
created before declaring the incident closed:

- Bookings with a wrong time or a failed calendar write
- Notifications sent — those cannot be recalled; tell the client
- Recordings uploaded under an unexpected key
- Calls stuck in `pending` post-processing

## After rolling back

1. Confirm `/healthz` on every service.
2. Place a real test call through the simulator, then a real phone call.
3. Run the evaluation suite: `uv run python -m ai_evals.cli`. Exit code 2
   means a safety property is still broken and the rollback did not
   restore a good state.
4. Check the admin system-health page for firing alerts.
5. Un-pause any tenant paused during containment, and tell them.

## Preventing the next one

- Additive migrations, deployed separately from the code that uses them.
- Every migration round-tripped on a Neon branch before it reaches
  staging.
- An evaluation case for any class of bug that reached production, so the
  same failure cannot return quietly.
