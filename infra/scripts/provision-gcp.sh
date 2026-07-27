#!/usr/bin/env bash
# One-time (idempotent) provisioning of Google Cloud resources for one
# environment. Safe to re-run; every command tolerates already-exists.
#
# Usage:
#   PROJECT_ID=my-project REGION=us-central1 ./infra/scripts/provision-gcp.sh
#
# Prerequisites: gcloud authenticated with owner/editor on the project.
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${REGION:?Set REGION (e.g. us-central1)}"

REPO_NAME="receptionist"
SERVICES=(api voice worker web)

echo "==> Enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iamcredentials.googleapis.com \
  --project "$PROJECT_ID"

echo "==> Creating Artifact Registry repository '$REPO_NAME'"
gcloud artifacts repositories create "$REPO_NAME" \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT_ID" \
  --description="Receptionist platform images (receptionist-api, receptionist-voice, receptionist-worker, receptionist-web)" \
  2>/dev/null || echo "    repository already exists"

echo "==> Creating per-service service accounts"
for svc in "${SERVICES[@]}"; do
  gcloud iam service-accounts create "receptionist-${svc}" \
    --display-name="Receptionist ${svc} runtime" \
    --project="$PROJECT_ID" \
    2>/dev/null || echo "    receptionist-${svc} already exists"
done

# Deployer identity used by GitHub Actions (via Workload Identity
# Federation — no exported keys).
echo "==> Creating deployer service account"
gcloud iam service-accounts create receptionist-deployer \
  --display-name="Receptionist CI deployer" \
  --project="$PROJECT_ID" \
  2>/dev/null || echo "    receptionist-deployer already exists"

echo "==> Granting deployer roles"
for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:receptionist-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="$role" \
    --condition=None \
    --quiet >/dev/null
done

# Runtime service accounts read only the secrets they need. Secrets are
# created per environment as "<NAME>__<environment>" by sync-secrets.sh;
# access is granted per secret there, not project-wide here.
echo "==> Provisioning complete for project $PROJECT_ID"
echo "    Next: ./infra/scripts/sync-secrets.sh to create environment secrets,"
echo "    then configure Workload Identity Federation for GitHub Actions"
echo "    (see infra/github-actions/README.md)."
