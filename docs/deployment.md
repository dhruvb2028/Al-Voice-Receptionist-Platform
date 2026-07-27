# Deployment

How code reaches production, and the shape of each environment.

## Environments

Three, with **fully isolated resources** — separate GCP projects,
separate Neon databases, separate provider accounts and phone numbers. A
staging test call must never be able to reach a real customer, and a
staging bug must never be able to write to production data.

| | Development | Staging | Production |
|---|---|---|---|
| Runs | Local machine | Cloud Run | Cloud Run |
| Database | Local Postgres (docker) | Neon staging branch | Neon production |
| Telephony | Simulator only | Twilio test number | Twilio live numbers |
| Providers | Mocks or dev keys | Dev keys | Production keys |
| Deploys on | — | Merge to `main` | Manual, tagged |
| Min instances | — | 0 | Voice: 1, API: 1 |

Development needs no cloud credentials at all: providers are mocked
behind their Protocols, the evaluation suite runs offline, and every
test that needs a database skips cleanly when one is not reachable.

## Pipeline

```
merge to main
  └─ CI (lint, types, tests, eval suite, migration verify)
      └─ build & push images
          └─ migration gate (staging)
              └─ deploy api, voice, worker (staging)

git tag v* (manual)
  └─ migration gate (production)
      └─ deploy api, voice, worker (production)
```

Staging deploys automatically after CI passes. Production is deliberately
manual — the gap between "tests pass" and "customers' calls depend on it"
should require a human decision.

Both deploy workflows skip cleanly when cloud variables are unset, so the
repository is usable before any infrastructure exists.

## Services

Definitions live in `infra/cloud-run/`. The constraints that matter:

**API** — autoscales from 1, `/healthz` liveness and `/readyz` startup
probes, graceful shutdown on SIGTERM, secrets injected from Secret
Manager, and connection pooling sized to the Neon plan.

**Voice** — the delicate one:

- CPU is *always allocated* (`cpu-throttling: false`). Audio must be
  processed between response phases; a throttled instance drops speech.
- Session affinity is on, because Twilio's media WebSocket must stay
  pinned to the instance holding the call.
- `containerConcurrency: 2` and `maxScale: 3` give a hard ceiling of six
  concurrent calls. Beyond that the webhook declines politely rather than
  degrading live calls — capacity is enforced, not hoped for.
- `timeoutSeconds: 3600` bounds the longest call; graceful shutdown lets
  in-flight calls finish rather than cutting a caller off mid-sentence.
- Production runs `minInstances: 1`. A cold start during a call is a
  caller hearing silence, so the warm instance is not optional there.

**Worker** — only reachable by QStash, every delivery signature-verified,
handlers idempotent, retries bounded, and exhausted jobs left in a
dead-letter state that is visible on the call record and the system-health
page rather than disappearing.

## Dashboard

Deployed to Cloudflare Pages. Required configuration:

| Variable | Purpose |
|---|---|
| `API_BASE_URL` | Production API origin (server-side only) |
| `NEXT_PUBLIC_APP_URL` | Canonical dashboard origin |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk client key |
| `CLERK_SECRET_KEY` | Clerk server key |
| `NEXT_PUBLIC_SENTRY_DSN` | Frontend error reporting |
| `SENTRY_AUTH_TOKEN` | Source-map upload at build time |

`src/env.ts` validates these at build and boot, so a missing or malformed
value fails the deploy rather than surfacing as a runtime error in front
of a client. Session cookies are issued by Clerk with `Secure`,
`HttpOnly`, and `SameSite=Lax`.

The API token never reaches the browser: the dashboard calls the API from
server components and route handlers only, and CSV exports are proxied
server-side for the same reason.

## Migrations

Never applied by hand. See the process in
[rollback.md](rollback.md) for the reverse direction; forward:

1. `infra/scripts/migrate.sh verify` — one head, and every migration
   reverses or declares `IRREVERSIBLE:` with a reason. Runs in CI.
2. `infra/scripts/migrate.sh branch <name>` — an ephemeral Neon branch
   cloned from production, so the migration is rehearsed against real
   data shapes and volumes rather than an empty schema.
3. `infra/scripts/migrate.sh test <branch-url>` — upgrade, downgrade,
   upgrade, then the schema test suite. A downgrade that fails here would
   have failed during a rollback, which is the worst moment to discover
   it.
4. Review the diff. Schema changes get read by a human, always.
5. Staging applies automatically on merge; run the smoke checks in
   [staging-checklist.md](staging-checklist.md).
6. Production applies on tag, gated on the same migration job.
7. Verify with [production-checklist.md](production-checklist.md).
8. Delete the Neon branch.

**Expand and contract.** A column rename ships as: add the new column,
backfill, write both, switch reads, stop writing the old, drop it — each
step separately deployable. A migration that requires code and schema to
change simultaneously cannot be rolled back independently, and so cannot
be rolled back at all.

## Secrets

No secret is in the repository. Values are stored in Secret Manager and
referenced as `sm://projects/<p>/secrets/<s>/versions/latest`;
`ai_shared/secrets.py` resolves either form, so local development uses
plain environment variables without a code path difference. Rotation is
in [secret-rotation.md](secret-rotation.md).

## Rollback

Redeploy the previous image. Details, including the case where a
migration has already applied, are in [rollback.md](rollback.md).
