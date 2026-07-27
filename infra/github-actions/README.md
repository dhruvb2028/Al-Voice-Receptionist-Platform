# CI/CD pipeline configuration

Reusable workflow fragments and deployment actions arrive with the
cloud-infrastructure milestone. The quality gate (lint, typecheck, test,
image builds) lives in [.github/workflows/ci.yml](../../.github/workflows/ci.yml).

Deployment order per environment: migrate database → deploy services →
smoke-check /healthz. Staging deploys on merge to main; production
requires manual approval.
