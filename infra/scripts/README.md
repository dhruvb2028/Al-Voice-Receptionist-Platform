# Operational scripts

| Script | Purpose |
|---|---|
| [provision-gcp.sh](provision-gcp.sh) | Idempotent GCP setup: APIs, Artifact Registry repo, runtime + deployer service accounts, IAM roles |
| [sync-secrets.sh](sync-secrets.sh) | Create/update per-environment secrets (`<VAR>__<env>`) from a local env file and grant least-privilege access per service |
| [deploy-service.sh](deploy-service.sh) | Deploy one service revision from a pushed image, then smoke-check it |

All scripts are bash, safe to re-run, and take configuration through
environment variables — see the header comment of each. Populated env
files used with `sync-secrets.sh` must never be committed.
