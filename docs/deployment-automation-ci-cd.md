# Deployment Automation And CI/CD Guide

This guide describes how to automate Scope deployments safely from repository
push to VPS release. It assumes the first production target is a single VPS
running:

- FastAPI backend
- Vite frontend served as static assets
- Postgres
- MinIO
- Nginx or Caddy reverse proxy
- systemd-managed application services

The goal is not just “auto deploy on push.” The goal is repeatable releases
with tests, migrations, backups, rollback paths, secret hygiene, and enough
checks that a bad commit does not quietly become production.

## Recommended Release Model

Use three environments:

```text
local       developer machine
staging     production-like VPS or separate staging stack
production  real users and real data
```

For the current team size, the simplest reliable model is:

```text
Pull request -> CI validation
main branch  -> deploy to staging
tag/release   -> deploy to production
```

Recommended branches:

- `feature/*`: active work
- `main`: integration branch, always deployable to staging
- version tags like `v0.3.0`: production releases

Avoid deploying every push directly to production. Research systems touch user
profiles, advisor memory, artifacts, paid provider APIs, and financial outputs,
so production should require an intentional release action.

## CI Pipeline

CI should run on every pull request and every push to `main`.

Minimum checks:

```bash
uv run python -m pytest tests/test_*.py -q
uv run python scripts/export_openapi.py
cd apps/web && npm ci && npm run build
```

Recommended checks before public beta:

```bash
uv run python scripts/validate_migrations.py
uv run python evaluations/advisor/deepeval_runner.py
uv run python -m pip_audit
cd apps/web && npm audit --omit=dev
```

Keep LLM-as-judge evals optional behind an env flag. Deterministic evals should
run by default.

## GitHub Actions CI Example

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  backend:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Install Python dependencies
        run: uv sync --frozen

      - name: Run backend tests
        run: uv run python -m pytest tests/test_*.py -q

      - name: Export OpenAPI contract
        run: uv run python scripts/export_openapi.py

      - name: Run deterministic advisor evals
        run: uv run python evaluations/advisor/deepeval_runner.py

  web:
    runs-on: ubuntu-24.04
    defaults:
      run:
        working-directory: apps/web
    steps:
      - uses: actions/checkout@v4

      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: apps/web/package-lock.json

      - name: Install web dependencies
        run: npm ci

      - name: Build web app
        run: npm run build
```

## Deployment Strategy

Use a pull-based deploy on the VPS for the first production version:

```text
GitHub Actions connects over SSH
  -> VPS pulls the release commit/tag
  -> installs locked dependencies
  -> builds frontend
  -> runs migrations
  -> restarts services
  -> health checks API and frontend
```

This is simpler than building containers immediately and is appropriate for a
single VPS. Move to Docker images later when you need multiple servers,
blue/green deployments, or stricter artifact immutability.

## Server Directory Layout

Recommended:

```text
/opt/scope/app              git checkout
/opt/scope/releases         optional future release snapshots
/opt/scope/backups          database and config backups
/var/lib/scope              runtime data if not using /opt volumes
/etc/scope/scope.env        production environment file
```

Environment file permissions:

```bash
sudo chown root:scope /etc/scope/scope.env
sudo chmod 640 /etc/scope/scope.env
```

Never store production secrets in GitHub Actions logs, repository files, or
frontend environment variables unless they are explicitly public browser config.

## Production Environment Contract

Production should fail fast if unsafe config is present.

Required production env:

```bash
SCOPE_ENV=production
SCOPE_DB_BACKEND=postgres
DATABASE_URL=postgresql://scope_user:<password>@127.0.0.1:5432/scope
SCOPE_REQUIRE_AUTH=true
SCOPE_ALLOWED_ORIGINS=https://app.example.com
AUTH_ALLOW_DEV_GOOGLE_TOKEN=false

ARTIFACT_STORE_BACKEND=minio
ARTIFACT_BUCKET=scope-artifacts
ARTIFACT_PREFIX=prod
ARTIFACT_S3_ENDPOINT_URL=http://127.0.0.1:9000
ARTIFACT_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=<minio-access-key>
AWS_SECRET_ACCESS_KEY=<minio-secret-key>

VITE_API_BASE_URL=https://api.example.com
```

Provider secrets:

```bash
OPENAI_API_KEY=...
GOOGLE_CLIENT_ID=...
EXA_API_KEY=...
```

Use separate keys for local, staging, and production.

## Deploy Script

Create `scripts/deploy_vps.sh` on the server or in the repository. A repository
script is easier to version, but keep secrets outside the repository.

Example:

```bash
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/scope/app"
ENV_FILE="/etc/scope/scope.env"
SERVICE_API="scope-api"

cd "$APP_DIR"

echo "Fetching latest code..."
git fetch --all --tags --prune

if [[ -n "${SCOPE_RELEASE_REF:-}" ]]; then
  git checkout "$SCOPE_RELEASE_REF"
else
  git checkout main
  git pull --ff-only origin main
fi

echo "Installing backend dependencies..."
uv sync --frozen

echo "Installing and building frontend..."
cd apps/web
npm ci
npm run build
cd "$APP_DIR"

echo "Running database migrations..."
set -a
source "$ENV_FILE"
set +a
uv run python scripts/migrate_db.py
uv run python scripts/validate_migrations.py

echo "Restarting services..."
sudo systemctl restart "$SERVICE_API"
sudo systemctl reload nginx || true

echo "Checking API health..."
curl -fsS "http://127.0.0.1:8000/health" >/dev/null

echo "Deployment complete."
```

Permissions:

```bash
chmod +x scripts/deploy_vps.sh
```

If using Caddy instead of Nginx, replace the reload command.

## systemd Service

Example `/etc/systemd/system/scope-api.service`:

```ini
[Unit]
Description=Scope API
After=network.target postgresql.service

[Service]
User=scope
Group=scope
WorkingDirectory=/opt/scope/app
EnvironmentFile=/etc/scope/scope.env
ExecStart=/opt/scope/app/.venv/bin/python api_main.py
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

Apply:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now scope-api
sudo systemctl status scope-api
```

## GitHub Actions Deployment Example

Create `.github/workflows/deploy-staging.yml`:

```yaml
name: Deploy Staging

on:
  push:
    branches: [main]

concurrency:
  group: deploy-staging
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-24.04
    environment: staging
    steps:
      - name: Deploy over SSH
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.STAGING_HOST }}
          username: scope
          key: ${{ secrets.STAGING_SSH_KEY }}
          script: |
            cd /opt/scope/app
            SCOPE_RELEASE_REF=main ./scripts/deploy_vps.sh
```

Create `.github/workflows/deploy-production.yml`:

```yaml
name: Deploy Production

on:
  push:
    tags:
      - "v*.*.*"

concurrency:
  group: deploy-production
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-24.04
    environment: production
    steps:
      - name: Deploy release over SSH
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.PROD_HOST }}
          username: scope
          key: ${{ secrets.PROD_SSH_KEY }}
          script: |
            cd /opt/scope/app
            SCOPE_RELEASE_REF="${{ github.ref_name }}" ./scripts/deploy_vps.sh
```

Use GitHub Environments for production approvals:

- Require manual approval for `production`.
- Restrict who can approve deployments.
- Store production secrets only in the production environment.

## Required GitHub Secrets

Staging:

- `STAGING_HOST`
- `STAGING_SSH_KEY`

Production:

- `PROD_HOST`
- `PROD_SSH_KEY`

Do not put application secrets in GitHub unless the deploy process needs them.
For the VPS model, prefer storing app secrets in `/etc/scope/scope.env` on the
server.

## Database Migrations

Rules:

- Every schema change must include an Alembic migration.
- CI must validate migrations against an empty database.
- Deployments must run migrations before restarting API code that expects the
new schema.
- Destructive migrations require a manual backup and an explicit rollback plan.

Pre-deploy backup:

```bash
pg_dump "$DATABASE_URL" | gzip > "/opt/scope/backups/scope-$(date +%Y%m%d-%H%M%S).sql.gz"
```

For early production, run this backup automatically before migrations.

## Artifact Storage Checks

Before deployment:

- Confirm MinIO is reachable only from localhost or private network.
- Confirm bucket exists.
- Confirm API credentials can write to the bucket.
- Confirm `artifact_manifest` records the written object.

Smoke command:

```bash
uv run python scripts/smoke_artifact_store.py
```

If that script does not exist yet, create one before public beta.

## Health Checks

Minimum checks after deploy:

```bash
curl -fsS https://api.example.com/health
curl -fsS https://app.example.com
```

Recommended checks:

- API health endpoint
- OpenAPI endpoint
- database connection check
- MinIO write/read smoke test
- Google OAuth callback flow
- authenticated user can load dashboard
- user can start a small research run
- advisor can answer from saved memory

Do not mark a production deploy successful until these pass.

## Rollback Strategy

For the pull-based VPS model:

```bash
cd /opt/scope/app
git checkout <previous-good-tag>
uv sync --frozen
cd apps/web && npm ci && npm run build
cd /opt/scope/app
sudo systemctl restart scope-api
sudo systemctl reload nginx
```

Database rollback is harder. Prefer forward-fix migrations. If a migration is
destructive, take a verified backup and document the restore command before
deployment.

Restore example:

```bash
gunzip -c /opt/scope/backups/scope-YYYYMMDD-HHMMSS.sql.gz | psql "$DATABASE_URL"
```

## Zero-Downtime Path Later

For a single VPS beta, a short restart is acceptable. For paid users, move
toward:

```text
build immutable image
run new API instance on alternate port
health check new instance
switch reverse proxy upstream
stop old instance
```

This can be done with Docker Compose:

- `scope-api-blue`
- `scope-api-green`
- Nginx/Caddy upstream switch

Or with a platform:

- Fly.io
- Render
- Railway
- ECS
- Kubernetes

Only add this once product usage justifies the operational complexity.

## Security Best Practices

Deployment must preserve the security posture:

- SSH key login only.
- Disable root SSH login.
- Disable password SSH login.
- UFW allows only `22`, `80`, and `443`.
- Postgres bound to `127.0.0.1` or private network.
- MinIO bound to `127.0.0.1` or private network.
- TLS enabled for frontend and API.
- Production CORS restricted to frontend domain.
- `AUTH_ALLOW_DEV_GOOGLE_TOKEN=false`.
- `SCOPE_REQUIRE_AUTH=true`.
- No public object-storage buckets.
- Logs must not include tokens, OAuth payloads, raw onboarding answers, or API keys.

## Release Checklist

Before tagging production:

- PR reviewed.
- Backend tests pass.
- Frontend build passes.
- OpenAPI export succeeds.
- Deterministic evals pass.
- Migration validation passes.
- Security-sensitive env checked.
- Backup completed.
- Rollback tag known.

After deployment:

- API health check passes.
- Frontend loads over HTTPS.
- Google sign-in works.
- Authenticated dashboard loads.
- A small research run can be created.
- Advisor conversation can be created and resumed.
- Logs show no startup errors.
- Metrics/alerts are receiving data.

## First Automation Milestone

The first milestone should be:

1. Add strict production env validation.
2. Add CI for tests, OpenAPI export, and web build.
3. Add `scripts/deploy_vps.sh`.
4. Set up staging deployment from `main`.
5. Only then enable production tag deployment.

That sequence gives us release safety before release speed.
