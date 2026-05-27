#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${SCOPE_APP_DIR:-/opt/scope/app}"
ENV_FILE="${SCOPE_ENV_FILE:-/etc/scope/scope.env}"
API_SERVICE="${SCOPE_API_SERVICE:-scope-api}"
WEB_DIR="$APP_DIR/apps/web"
HEALTH_URL="${SCOPE_HEALTH_URL:-http://127.0.0.1:8000/health}"

cd "$APP_DIR"

echo "Fetching release..."
git fetch --all --tags --prune
if [[ -n "${SCOPE_RELEASE_REF:-}" ]]; then
  git checkout "$SCOPE_RELEASE_REF"
else
  git checkout main
  git pull --ff-only origin main
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

echo "Installing backend dependencies..."
uv sync --frozen

echo "Checking production config..."
uv run python scripts/check_production_config.py "$ENV_FILE"

echo "Installing and building frontend..."
cd "$WEB_DIR"
npm ci
npm run build
cd "$APP_DIR"

echo "Backing up Postgres..."
SCOPE_ENV_FILE="$ENV_FILE" scripts/backup_postgres.sh

echo "Running migrations..."
uv run python scripts/migrate_db.py
uv run python scripts/validate_migrations.py

echo "Restarting API service..."
sudo systemctl restart "$API_SERVICE"
sudo systemctl reload nginx || true

echo "Checking API health..."
curl -fsS "$HEALTH_URL" >/dev/null

echo "Deployment complete"
