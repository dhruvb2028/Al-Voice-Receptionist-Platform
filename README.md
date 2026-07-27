# AI Voice Receptionist Platform

A multi-tenant, cloud-hosted AI voice receptionist for home-services businesses (plumbing, HVAC, electrical). The platform answers inbound phone calls, holds a natural spoken conversation, checks real calendar availability, books appointments safely, takes messages, and escalates to a human when needed — while giving each business a dashboard to configure their receptionist and review every call.

## What it does

- **Answers inbound calls** on a business's phone number and greets callers with a tenant-approved greeting
- **Streams speech both ways** — realtime transcription, low-latency LLM responses, streaming voice synthesis, and caller barge-in
- **Books appointments** against the business's real Google Calendar, with transactional safety and idempotency (no double-booking, no confirmation before the write succeeds)
- **Takes messages and classifies urgency**, escalating emergencies and explicit human requests to a live transfer
- **Never invents facts** — prices, services, hours, and service areas come only from the business's configured data
- **Multi-tenant by design** — one platform, many businesses, hard isolation between tenants at both the application and database layer

## Architecture

Four deployable services split along the realtime latency boundary:

| Service | Role |
|---|---|
| `web` | Next.js dashboard for clients and platform admins |
| `api` | FastAPI control plane — tenants, configuration, webhooks, domain data |
| `voice` | Pipecat realtime orchestrator — media streams, STT, LLM, TTS, conversation state |
| `worker` | Post-call processing — transcripts, summaries, recordings, notifications |

**Stack:** Next.js · TypeScript · Tailwind · shadcn/ui · FastAPI · Python 3.12 · SQLAlchemy 2 · Neon PostgreSQL · Upstash Redis/QStash · Twilio · Deepgram · Groq · Cartesia · Clerk · Cloudflare R2 · Google Cloud Run · GitHub Actions

See [docs/architecture/00-assessment.md](docs/architecture/00-assessment.md) for the full system design: service boundaries, data flows, security model, deployment architecture, and risk analysis.

## Project status

🚧 In active development. The architecture baseline is complete; implementation is proceeding in phases — foundation, control plane, conversation engine, voice path, dashboards, and launch hardening.
