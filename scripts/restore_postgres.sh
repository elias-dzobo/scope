#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${SCOPE_ENV_FILE:-/etc/scope/scope.env}"
BACKUP_FILE="${1:-}"

if [[ -z "$BACKUP_FILE" ]]; then
  echo "Usage: scripts/restore_postgres.sh /path/to/backup.sql.gz" >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required for Postgres restore" >&2
  exit 1
fi

echo "Restoring Postgres backup into configured DATABASE_URL..."
gunzip -c "$BACKUP_FILE" | psql "$DATABASE_URL"
echo "Restore complete"
