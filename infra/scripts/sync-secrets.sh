#!/usr/bin/env bash
# Create/update Secret Manager secrets for one environment from a local
# env file, and grant each runtime service account access to exactly the
# secrets its service needs.
#
# Usage:
#   PROJECT_ID=my-project ENVIRONMENT=staging ENV_FILE=.env.staging \
#     ./infra/scripts/sync-secrets.sh
#
# Secret naming: <VAR_NAME>__<environment>  (e.g. DATABASE_URL__staging)
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${ENVIRONMENT:?Set ENVIRONMENT (dev|staging|production)}"
: "${ENV_FILE:?Set ENV_FILE (path to populated env file — never committed)}"

# Which service accounts may read which variables.
api_vars=(DATABASE_URL DATABASE_DIRECT_URL UPSTASH_REDIS_REST_URL UPSTASH_REDIS_REST_TOKEN \
  CLERK_SECRET_KEY CLERK_WEBHOOK_SECRET TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN \
  GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET RESEND_API_KEY \
  CALL_TOKEN_SIGNING_KEY INTERNAL_SERVICE_TOKEN SENTRY_DSN QSTASH_TOKEN)
voice_vars=(DATABASE_URL UPSTASH_REDIS_REST_URL UPSTASH_REDIS_REST_TOKEN \
  DEEPGRAM_API_KEY GROQ_API_KEY CARTESIA_API_KEY QSTASH_TOKEN \
  GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET \
  CALL_TOKEN_SIGNING_KEY INTERNAL_SERVICE_TOKEN SENTRY_DSN)
worker_vars=(DATABASE_URL UPSTASH_REDIS_REST_URL UPSTASH_REDIS_REST_TOKEN \
  QSTASH_CURRENT_SIGNING_KEY QSTASH_NEXT_SIGNING_KEY TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN \
  R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY \
  GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET \
  GROQ_API_KEY RESEND_API_KEY SENTRY_DSN)
web_vars=(CLERK_SECRET_KEY API_BASE_URL SENTRY_DSN)

upsert_secret() {
  local name="$1" value="$2" secret_id
  secret_id="${name}__${ENVIRONMENT}"
  if ! gcloud secrets describe "$secret_id" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$secret_id" --replication-policy=automatic --project="$PROJECT_ID"
  fi
  printf '%s' "$value" | gcloud secrets versions add "$secret_id" \
    --data-file=- --project="$PROJECT_ID" >/dev/null
  echo "    upserted $secret_id"
}

grant() {
  local svc="$1" var="$2" secret_id
  secret_id="${var}__${ENVIRONMENT}"
  gcloud secrets add-iam-policy-binding "$secret_id" \
    --member="serviceAccount:receptionist-${svc}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT_ID" --quiet >/dev/null
}

echo "==> Upserting secrets from $ENV_FILE"
while IFS='=' read -r key value; do
  [[ -z "$key" || "$key" == \#* ]] && continue
  [[ -z "$value" ]] && continue
  upsert_secret "$key" "$value"
done < "$ENV_FILE"

echo "==> Granting per-service access"
for v in "${api_vars[@]}";    do grant api "$v"    2>/dev/null || true; done
for v in "${voice_vars[@]}";  do grant voice "$v"  2>/dev/null || true; done
for v in "${worker_vars[@]}"; do grant worker "$v" 2>/dev/null || true; done
for v in "${web_vars[@]}";    do grant web "$v"    2>/dev/null || true; done

echo "==> Secrets synced for environment: $ENVIRONMENT"
