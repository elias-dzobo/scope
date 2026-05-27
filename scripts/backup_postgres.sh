#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${SCOPE_BACKUP_DIR:-/opt/scope/backups}"
ENV_FILE="${SCOPE_ENV_FILE:-/etc/scope/scope.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required for Postgres backup" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

OUT="$BACKUP_DIR/scope-$(date -u +%Y%m%d-%H%M%S).sql.gz"
pg_dump "$DATABASE_URL" | gzip > "$OUT"
chmod 600 "$OUT"
echo "Postgres backup written: $OUT"
