#!/usr/bin/env bash
# Deploy one service revision to Cloud Run from an already-pushed image.
#
# Usage:
#   PROJECT_ID=p REGION=us-central1 ENVIRONMENT=staging IMAGE=...:tag \
#     ./infra/scripts/deploy-service.sh api
set -euo pipefail

SERVICE="${1:?Usage: deploy-service.sh <api|voice|worker|web>}"
: "${PROJECT_ID:?Set PROJECT_ID}"
: "${REGION:?Set REGION}"
: "${ENVIRONMENT:?Set ENVIRONMENT}"
: "${IMAGE:?Set IMAGE (full Artifact Registry image ref)}"

case "$ENVIRONMENT" in
  production) MIN_INSTANCES_DEFAULT=1 ;;
  *)          MIN_INSTANCES_DEFAULT=0 ;;
esac
export PROJECT_ID REGION ENVIRONMENT IMAGE
export MIN_INSTANCES="${MIN_INSTANCES:-$MIN_INSTANCES_DEFAULT}"

MANIFEST="infra/cloud-run/${SERVICE}.yaml"
[[ -f "$MANIFEST" ]] || { echo "Unknown service: $SERVICE"; exit 1; }

echo "==> Deploying receptionist-${SERVICE}-${ENVIRONMENT} ($IMAGE)"
envsubst < "$MANIFEST" | gcloud run services replace - \
  --region="$REGION" --project="$PROJECT_ID"

URL=$(gcloud run services describe "receptionist-${SERVICE}-${ENVIRONMENT}" \
  --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')

echo "==> Smoke check"
if [[ "$SERVICE" == "web" ]]; then path="/"; else path="/healthz"; fi
for i in $(seq 1 20); do
  if curl -fsS "${URL}${path}" >/dev/null 2>&1; then
    echo "    ${URL}${path} healthy"
    exit 0
  fi
  sleep 5
done
echo "!! Smoke check failed for ${URL}${path}" >&2
exit 1
