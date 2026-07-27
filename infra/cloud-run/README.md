# Cloud Run deployment configuration

Declarative Knative service manifests, deployed via
`infra/scripts/deploy-service.sh` (envsubst + `gcloud run services replace`).

| Manifest | Service | Key settings |
|---|---|---|
| [api.yaml](api.yaml) | control plane | min 0 (dev/stg) / 1 (prod), max 3, concurrency 20, 60 s timeout |
| [voice.yaml](voice.yaml) | realtime calls | max 3, 2 calls/instance (6-call hard cap), CPU always on, session affinity, 3600 s timeout, graceful drain |
| [worker.yaml](worker.yaml) | post-call jobs | scale-to-zero, QStash signature-verified, idempotent, 300 s timeout |
| [web.yaml](web.yaml) | dashboard | Next.js standalone, concurrency 80 |

All services: startup probe `/readyz`, liveness probe `/healthz`
(dashboard probes `/`), non-root containers, secrets injected from
Secret Manager at deploy — never baked into images.

Substitution variables: `PROJECT_ID`, `REGION`, `ENVIRONMENT`, `IMAGE`,
`MIN_INSTANCES` (defaulted per environment by the deploy script).
