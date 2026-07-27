#!/usr/bin/env bash
# Controlled migration runner.
#
# A schema change reaches production through the same path every time:
#
#   verify -> branch -> apply -> test -> (review) -> staging -> production
#
# The script refuses to skip a step. Its most important behaviour is the
# check that a migration is reversible *before* it is applied anywhere,
# because discovering a missing downgrade during an incident is too late.
#
# Usage:
#   infra/scripts/migrate.sh verify              # offline checks only
#   infra/scripts/migrate.sh branch <name>       # ephemeral Neon branch
#   infra/scripts/migrate.sh apply <url>         # upgrade head
#   infra/scripts/migrate.sh test <url>          # upgrade, downgrade, upgrade
#   infra/scripts/migrate.sh status <url>
set -euo pipefail

COMMAND="${1:-help}"

die() { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }

require_url() {
  [[ -n "${1:-}" ]] || die "a database URL is required"
}

# --- verify -----------------------------------------------------------------
# Offline checks. These run in CI on every pull request touching migrations.
verify() {
  info "checking for a single migration head"
  local heads
  heads="$(uv run alembic heads | grep -c . || true)"
  [[ "$heads" == "1" ]] || die "expected exactly one head, found $heads (branches must be merged)"

  info "checking every migration defines a downgrade"
  # A few operations genuinely cannot be undone — PostgreSQL cannot drop
  # a value from an enum type, for instance. Those migrations declare
  # `IRREVERSIBLE:` with a reason, so the exception is visible in review
  # rather than hidden behind a silent `pass`.
  local missing=0
  while IFS= read -r file; do
    if grep -q 'IRREVERSIBLE:' "$file"; then
      continue
    fi
    if ! grep -qE '^def downgrade\(\)' "$file"; then
      echo "  $file: no downgrade()" >&2
      missing=1
    elif [[ "$(awk '/^def downgrade\(\)/{flag=1;next}/^def /{flag=0}flag' "$file" \
              | grep -vE '^\s*(#|$|"""|\x27\x27\x27)' | tr -d '[:space:]')" == "pass" ]]; then
      echo "  $file: downgrade() is a no-op and is not marked IRREVERSIBLE:" >&2
      missing=1
    fi
  done < <(find migrations/versions -name '*.py' -not -name '__*')
  [[ "$missing" == "0" ]] || die "every migration must be reversible or declare why not"

  info "verify passed"
}

# --- branch -----------------------------------------------------------------
# An ephemeral Neon branch is a copy-on-write clone of production data, so
# a migration is rehearsed against real shapes and volumes rather than an
# empty schema.
branch() {
  local name="${1:-}"
  [[ -n "$name" ]] || die "usage: migrate.sh branch <name>"
  command -v neonctl >/dev/null || die "neonctl is required (npm i -g neonctl)"
  [[ -n "${NEON_PROJECT_ID:-}" ]] || die "NEON_PROJECT_ID must be set"

  info "creating Neon branch '$name' from production"
  neonctl branches create \
    --project-id "$NEON_PROJECT_ID" \
    --name "$name" \
    --parent production
  info "connection string:"
  neonctl connection-string "$name" --project-id "$NEON_PROJECT_ID"
  echo
  info "delete it when finished: neonctl branches delete $name --project-id \$NEON_PROJECT_ID"
}

# --- apply ------------------------------------------------------------------
apply() {
  local url="${1:-}"
  require_url "$url"
  info "current revision"
  DATABASE_DIRECT_URL="$url" uv run alembic current
  info "upgrading to head"
  DATABASE_DIRECT_URL="$url" uv run alembic upgrade head
  info "now at"
  DATABASE_DIRECT_URL="$url" uv run alembic current
}

# --- test -------------------------------------------------------------------
# Round-trips the migration. A downgrade that fails here would have failed
# during a rollback, which is the worst possible moment to find out.
test_roundtrip() {
  local url="${1:-}"
  require_url "$url"
  local before
  before="$(DATABASE_DIRECT_URL="$url" uv run alembic current 2>/dev/null | head -1)"

  info "upgrade head"
  DATABASE_DIRECT_URL="$url" uv run alembic upgrade head
  info "downgrade -1"
  DATABASE_DIRECT_URL="$url" uv run alembic downgrade -1
  info "upgrade head again"
  DATABASE_DIRECT_URL="$url" uv run alembic upgrade head

  info "running the schema test suite against this database"
  TEST_DATABASE_URL="$url" uv run pytest -q packages/database/tests

  info "round-trip passed (started from: ${before:-base})"
}

status() {
  local url="${1:-}"
  require_url "$url"
  DATABASE_DIRECT_URL="$url" uv run alembic current -v
}

case "$COMMAND" in
  verify) verify ;;
  branch) branch "${2:-}" ;;
  apply) apply "${2:-}" ;;
  test) test_roundtrip "${2:-}" ;;
  status) status "${2:-}" ;;
  *)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
