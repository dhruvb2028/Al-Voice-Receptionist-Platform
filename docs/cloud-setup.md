# Cloud Setup

Reproducible setup for every hosted resource the platform depends on. Nothing in production depends on localhost, tunnels, or a laptop staying online. All credentials land in Google Secret Manager via `infra/scripts/sync-secrets.sh` — never in the repository.

**Related:** [system architecture §4](system-architecture.md) · [infra/cloud-run](../infra/cloud-run) · [infra/scripts](../infra/scripts)

---

## 1. Google Cloud (compute backbone)

One GCP project per environment tier is not required at this scale; a single project hosts all three environments, separated by resource naming (`receptionist-<service>-<environment>`) and per-environment secrets (`<VAR>__<environment>`).

```bash
PROJECT_ID=<project> REGION=us-central1 ./infra/scripts/provision-gcp.sh
```

The script is idempotent and creates:

| Resource | Value |
|---|---|
| APIs | Cloud Run, Artifact Registry, Secret Manager, IAM Credentials |
| Artifact Registry | Docker repo `receptionist` holding `receptionist-api`, `receptionist-voice`, `receptionist-worker`, `receptionist-web` images |
| Runtime service accounts | `receptionist-api@`, `receptionist-voice@`, `receptionist-worker@`, `receptionist-web@` — least privilege, secret access granted per secret |
| Deployer | `receptionist-deployer@` with `run.admin`, `artifactregistry.writer`, `iam.serviceAccountUser`; used by GitHub Actions via Workload Identity Federation (no exported keys) |

### Cloud Run services

Declarative manifests in [infra/cloud-run/](../infra/cloud-run) (deployed by `deploy-service.sh` with envsubst):

| Service | Min (dev/stg → prod) | Max | Concurrency | Timeout | Notes |
|---|---|---|---|---|---|
| api | 0 → 1 | 3 | 20 | 60 s | startup probe `/readyz`, liveness `/healthz` |
| voice | 0 → 1 | 3 | **2 calls/instance** | 3600 s | WebSockets, CPU always allocated, session affinity, graceful drain; 2×3 = **6-call hard cap** (webhook rejects beyond capacity) |
| worker | 0 | 5 | 10 | 300 s | QStash-triggered, signature-verified, idempotent jobs |
| web | 0 | 3 | 80 | 60 s | Next.js standalone |

---

## 2. Hosted resources

### Neon (PostgreSQL)

1. Create a project (region matching `$REGION`).
2. Databases: `receptionist_production` on the main branch; create branches `staging` and `dev` (instant copy-on-write, separate connection strings).
3. Enable **pgvector** per database: `CREATE EXTENSION IF NOT EXISTS vector;`
4. Collect both connection strings per environment: **pooled** (`DATABASE_URL`, for services) and **direct** (`DATABASE_DIRECT_URL`, for Alembic DDL).

### Clerk (authentication)

1. Create **one Clerk application per environment** (dev, staging, production) — isolation of users, orgs, and keys.
2. Enable **Organizations**; each tenant maps to one organization.
3. Configure session token: add the active organization ID and role claims.
4. Create a JWT template for the API audience; record `CLERK_JWT_ISSUER` and `CLERK_JWT_AUDIENCE`.
5. Add a webhook endpoint (`https://<api-url>/webhooks/clerk`) for `user.*` and `organization.*` events; record `CLERK_WEBHOOK_SECRET`.
6. Collect `CLERK_SECRET_KEY` and `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`.

### Upstash Redis

1. One database per environment (`receptionist-dev/staging/production`), same region as Cloud Run.
2. Key convention: `tenant:{tenant_id}:...` — enforced by the cache layer.
3. Collect `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`.

### Upstash QStash

1. One QStash topic set per environment; destinations point at the environment's worker URL (`https://receptionist-worker-<env>-....run.app`).
2. Configure retries (5, exponential backoff) and a dead-letter queue.
3. Collect `QSTASH_TOKEN`, `QSTASH_CURRENT_SIGNING_KEY`, `QSTASH_NEXT_SIGNING_KEY`.

### Cloudflare R2 (recordings)

1. One bucket per environment: `receptionist-recordings-<env>`. No public access.
2. Object keys: `tenants/{tenant_id}/calls/{call_id}/recording.wav`.
3. Create an API token scoped to the bucket (read/write); collect `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_ENDPOINT`.

### Sentry

1. One Sentry project per service (`receptionist-api`, `-voice`, `-worker`, `-web`); use the `environment` tag (`dev`/`staging`/`production`) to separate tiers.
2. Alert rules: new issue in production, error-rate spike, and (later) latency regressions.
3. Collect `SENTRY_DSN` per service and `NEXT_PUBLIC_SENTRY_DSN` for the dashboard.

### Twilio

1. Separate subaccounts for staging and production (isolated numbers, logs, and billing); dev shares staging.
2. Buy test numbers under staging; port/buy real tenant numbers under production only.
3. Point each number's Voice webhook at `https://<api-url>/webhooks/twilio/voice` (HTTP POST).
4. Collect `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` per subaccount; set `TWILIO_WEBHOOK_BASE_URL` to the environment's API URL.

### Deepgram

1. One project; create **separate API keys per environment** (usage attribution + revocation).
2. Model: `nova-3` streaming, English; endpointing enabled at the SDK level.
3. Collect `DEEPGRAM_API_KEY`.

### Groq

1. One account; separate API keys per environment.
2. Default model recorded in `GROQ_MODEL` (see `.env.example`) so model upgrades are config changes.
3. Collect `GROQ_API_KEY`.

### Cartesia

1. One account; separate API keys per environment.
2. Select the tenant-selectable voice list from the Sonic catalog; record voice IDs in tenant configuration, not code.
3. Collect `CARTESIA_API_KEY`.

### Google Calendar (OAuth)

1. In a Google Cloud project (can be the platform project): enable the **Calendar API**.
2. Configure the OAuth consent screen (external, production status; scopes: `calendar.events`, `calendar.readonly`).
3. Create OAuth client (web application); authorized redirect: `https://<api-url>/integrations/google/callback` per environment.
4. Collect `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`.

### Resend (email)

1. Verify the sending domain (SPF + DKIM records).
2. Separate API keys per environment; staging sends only to platform-internal addresses (enforced in worker config).
3. Collect `RESEND_API_KEY`, `EMAIL_FROM`.

---

## 3. Environment separation

| Concern | dev | staging | production |
|---|---|---|---|
| Database | Neon branch `dev` | Neon branch `staging` | Neon main branch |
| Redis | `receptionist-dev` db | `receptionist-staging` db | `receptionist-production` db |
| R2 | `receptionist-recordings-dev` | `-staging` | `-production` |
| Clerk | dev application | staging application | production application |
| Twilio | staging subaccount (test numbers) | staging subaccount | production subaccount (real numbers) |
| Provider keys | per-env keys | per-env keys | per-env keys |
| Sentry | `environment=dev` | `environment=staging` | `environment=production` |
| Secrets | `<VAR>__dev` | `<VAR>__staging` | `<VAR>__production` |
| Cloud Run | `*-dev` services | `*-staging` services | `*-production` services |
| Deploy trigger | manual script | merge to `main` | manual, approved |

**No credential crosses an environment boundary.** A leaked staging key exposes staging only.

---

## 4. Deployment pipeline

Workflows in [.github/workflows](../.github/workflows):

- **ci.yml** — quality gate: Python lint/typecheck/test, dashboard lint/typecheck/build, container builds. Runs on every PR, and as a called workflow inside the staging deploy.
- **deploy-staging.yml** — on merge to `main`: quality gate → build & push all four images to Artifact Registry → **migration gate** (Alembic against staging) → deploy all services with smoke checks. Skips cloud steps cleanly until `GCP_PROJECT_ID`/`GCP_WORKLOAD_IDENTITY_PROVIDER` repository variables are configured.
- **deploy-production.yml** — manual `workflow_dispatch` only. Takes a staging-verified image tag; the GitHub `production` environment requires reviewer approval; **migrations run only when the `run_migrations` input is explicitly set** — never automatically.

GitHub configuration required (Settings):

| Item | Where | Values |
|---|---|---|
| `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER` | Repository variables | from provisioning |
| `STAGING_DATABASE_DIRECT_URL` | `staging` environment secret | Neon staging direct URL |
| `PRODUCTION_DATABASE_DIRECT_URL` | `production` environment secret | Neon production direct URL |
| `production` environment | Environments | required reviewers enabled |

Workload Identity Federation setup (one-time): create a workload identity pool + GitHub OIDC provider restricted to this repository, and allow it to impersonate `receptionist-deployer@`. See [infra/github-actions/README.md](../infra/github-actions/README.md).
