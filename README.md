# AI Voice Receptionist Platform

A multi-tenant, cloud-hosted AI voice receptionist for home-services businesses (plumbing, HVAC, electrical). The platform answers inbound phone calls, holds a natural spoken conversation, checks real calendar availability, books appointments safely, takes messages, and escalates to a human when needed — while giving each business a dashboard to configure their receptionist and review every call.

## What it does

- **Answers inbound calls** on a business's phone number and greets callers with a tenant-approved greeting
- **Streams speech both ways** — realtime transcription, low-latency LLM responses, streaming voice synthesis, and caller barge-in
- **Books appointments** against the business's real Google Calendar, with transactional safety and idempotency (no double-booking, no confirmation before the write succeeds)
- **Takes messages and classifies urgency**, escalating emergencies and explicit human requests to a live transfer
- **Never invents facts** — prices, services, hours, and service areas come only from the business's configured data
- **Multi-tenant by design** — one platform, many businesses, hard isolation between tenants at both the application and database layer

## Repository structure

```
├── apps/
│   └── dashboard/        Next.js dashboard (clients + platform admins)
├── services/
│   ├── api/              FastAPI control plane: tenants, config, webhooks
│   ├── voice/            Realtime voice orchestrator: media, STT, LLM, TTS
│   └── worker/           Post-call processor: transcripts, notifications
├── packages/
│   ├── domain/           Domain models and business rules (shared)
│   ├── database/         Async SQLAlchemy engine + tenant-scoped repositories
│   ├── providers/        Provider interfaces and adapters
│   ├── telemetry/        Structured logging with redaction
│   └── shared/           Settings, error format, request IDs
├── migrations/           Alembic migrations (owned by the API service)
├── evals/                Conversation evaluation harness
├── infra/                Cloud Run, CI/CD, and operational scripts
├── docs/                 Architecture, requirements, security specs
└── .github/workflows/    Quality gate: lint, typecheck, test, image builds
```

## Service responsibilities

| Service | Role | Runtime |
|---|---|---|
| `dashboard` | Client + admin UI, auth-aware routing, configuration forms, call review, test console | Next.js (App Router, TypeScript strict) |
| `api` | REST APIs, tenant management, business configuration, queries, provider webhooks, call tokens | FastAPI · Python 3.12 |
| `voice` | Twilio Media Streams, streaming STT/LLM/TTS, conversation state machine, tools, barge-in, telemetry | FastAPI + Pipecat · Python 3.12 |
| `worker` | Post-call extraction, summaries, notifications, usage aggregation, recording lifecycle, reconciliation | FastAPI · Python 3.12 |

Full design: [system architecture](docs/system-architecture.md) · [call lifecycle](docs/call-lifecycle.md) · [security boundaries](docs/security-boundaries.md) · [product requirements](docs/product-requirements.md)

## Development workflow

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
make install          # install Python workspace + dashboard dependencies
make check            # full quality gate: lint, typecheck, test

make dev-api          # FastAPI control plane on :8000
make dev-voice        # voice service on :8001
make dev-worker       # worker on :8002
make dev-dashboard    # Next.js dashboard on :3000
```

Copy `.env.example` to `.env` for local configuration. Services validate their environment at startup and fail fast on malformed values.

## Test commands

```bash
make test             # all Python tests (pytest, async mode)
make typecheck        # mypy --strict + tsc --noEmit
make lint             # ruff + eslint
```

CI runs the same gate on every push and pull request, plus container-image builds for all three Python services.

## Environment strategy

| Environment | Purpose | Deploy trigger |
|---|---|---|
| `local` | Development against local services + test provider credentials | manual |
| `staging` | Production-shaped, Twilio test numbers, full stack on Cloud Run | merge to `main` |
| `production` | Live tenants and real numbers | manual approval |

Every feature must be exercisable in a hosted environment — no production capability may depend on localhost, tunnels, or a laptop staying online. Secrets live in Google Secret Manager and are injected at deploy; nothing sensitive is committed or baked into images.

## Cloud deployment workflow

1. CI quality gate passes (lint, typecheck, tests, image builds).
2. Images are built and pushed to Google Artifact Registry.
3. Database migrations run as a separate job — always backward-compatible with the running revision.
4. Cloud Run services deploy: `web`, `api`, `voice` (CPU always allocated, session affinity), `worker`.
5. Smoke checks hit each service's `/healthz`; failures roll back.

## Migration strategy

- Alembic owns the schema; migrations live in `migrations/` and are autogenerated against the shared metadata (`make migration m="..."`), then hand-reviewed.
- Migrations run **before** service deploys and must be compatible with both the old and new code (expand → migrate → contract for breaking changes).
- `DATABASE_DIRECT_URL` (unpooled) is used for DDL; services use the pooled `DATABASE_URL`.

## Project status

🚧 In active development. Foundation complete: monorepo, quality gate, service skeletons with health endpoints, structured logging with redaction, standard error envelope, and container images. Next: cloud infrastructure provisioning and the database schema.
