# Cloud Run deployment configuration

Service definitions and deploy scripts arrive with the cloud-infrastructure
milestone. Per-service requirements are documented in
[docs/system-architecture.md](../../docs/system-architecture.md) §4:

| Service | Min instances | Notes |
|---|---|---|
| web | 0 | Next.js standalone |
| api | 1 | webhook latency |
| voice | 1 | CPU always allocated, session affinity, 60 min timeout |
| worker | 0 | woken by QStash |
