# Deployment Checklist

This checklist is the operational runbook for deploying Scope to a VPS.

## 1. Pre-Deploy Decision

Target stage:

- [ ] local development
- [ ] staging
- [ ] private beta
- [ ] public beta
- [ ] production

Public beta/production requires all P0 items in
`docs/production-readiness-audit.md` and `docs/security-audit.md`.

## 2. Required Accounts

- [ ] VPS provider account
- [ ] Domain registrar / DNS provider
- [ ] Google OAuth client
- [ ] OpenAI API key
- [ ] Gemini API key
- [ ] Exa/Tavily/SerpAPI search keys as needed
- [ ] Email or incident notification channel

## 3. Server Provisioning

- [ ] Ubuntu 22.04/24.04 LTS
- [ ] SSH key access only
- [ ] Password login disabled
- [ ] UFW enabled
- [ ] Ports open: `22`, `80`, `443`
- [ ] App user created: `scope`
- [ ] Repo deployed to `/opt/scope`
- [ ] Runtime env stored at `/etc/scope/scope.env`

## 4. DNS

Recommended records:

```text
app.yourdomain.com  A  <VPS_IP>
api.yourdomain.com  A  <VPS_IP>
```

Checklist:

- [ ] DNS resolves
- [ ] Frontend domain chosen
- [ ] API domain chosen
- [ ] Google OAuth authorized JavaScript origin configured
- [ ] Frontend `VITE_API_BASE_URL` points at API domain

## 5. Environment

Backend required:

```bash
SCOPE_ENV=production
SCOPE_DB_BACKEND=postgres
DATABASE_URL=postgresql://...
SCOPE_AUTO_MIGRATE=false
ARTIFACT_STORE_BACKEND=minio
ARTIFACT_BUCKET=scope-artifacts
ARTIFACT_PREFIX=scope
ARTIFACT_S3_ENDPOINT_URL=http://127.0.0.1:9000
JWT_SECRET=<long random secret>
GOOGLE_CLIENT_ID=<prod client id>
AUTH_ALLOW_DEV_GOOGLE_TOKEN=false
```

Provider keys:

```bash
OPENAI_API_KEY=...
GEMINI_API_KEY=...
EXA_API_KEY=...
```

Production hardening:

```bash
SCOPE_ALLOWED_ORIGINS=https://app.yourdomain.com
SCOPE_REQUIRE_AUTH=true
RESEARCH_RATE_LIMIT_PER_MIN=30
RESEARCH_RATE_LIMIT_BURST=10
```

Checklist:

- [ ] `.env` not deployed as source of truth
- [ ] `/etc/scope/scope.env` permissions are `640`
- [ ] secrets are not printed in logs
- [ ] staging/prod keys are separate

## 6. Database

Postgres:

- [ ] database created
- [ ] least-privilege app user created
- [ ] database not exposed publicly
- [ ] migrations run

Commands:

```bash
cd /opt/scope
uv run python scripts/migrate_db.py
uv run python scripts/validate_migrations.py
```

Backup:

- [ ] daily `pg_dump`
- [ ] restore rehearsal completed
- [ ] backup retention documented

## 7. MinIO

- [ ] MinIO running
- [ ] bound to localhost/private network
- [ ] bucket created
- [ ] bucket is private
- [ ] app can write artifact
- [ ] backup/snapshot strategy configured

Smoke test:

```bash
ARTIFACT_STORE_BACKEND=minio uv run python -m pytest tests/test_artifact_store.py -q
```

## 8. Backend Service

Recommended process:

- `scope-api.service`
- optional future `scope-worker.service`

Checklist:

- [ ] `uv sync --frozen` or equivalent dependency install
- [ ] service starts
- [ ] service restarts on failure
- [ ] logs go to journald
- [ ] `/health` returns ok
- [ ] `/api/v1/health` returns ok

Health:

```bash
curl https://api.yourdomain.com/health
curl https://api.yourdomain.com/api/v1/health
```

## 9. Frontend

Build:

```bash
cd /opt/scope/apps/web
npm ci
npm run build
```

Checklist:

- [ ] `VITE_API_BASE_URL` set for production
- [ ] Google client id configured
- [ ] static files served from `apps/web/dist`
- [ ] cache headers set
- [ ] security headers set

## 10. Reverse Proxy

Use Caddy or Nginx.

Checklist:

- [ ] TLS enabled
- [ ] frontend routes to static app
- [ ] API routes to backend
- [ ] request body limits configured
- [ ] auth and research-create endpoints rate-limited
- [ ] MinIO/Postgres not proxied publicly

## 11. Verification

Backend:

```bash
uv run python -m pytest tests/test_*.py -q
uv run python scripts/export_openapi.py
uv run python scripts/validate_migrations.py
```

Frontend:

```bash
cd apps/web
npm run build
```

Manual smoke:

- [ ] landing page loads
- [ ] Google sign-in works
- [ ] onboarding save works
- [ ] start research run works
- [ ] run progress updates
- [ ] completed research renders
- [ ] ask advisor about completed research opens anchored conversation
- [ ] advisor follow-up stays in thread context
- [ ] saved research library loads
- [ ] logout clears session

## 12. Rollback

Before deploy:

- [ ] record current git SHA
- [ ] database backup taken
- [ ] MinIO backup/snapshot taken

Rollback:

```bash
cd /opt/scope
git checkout <previous_sha>
uv sync
cd apps/web && npm ci && npm run build
sudo systemctl restart scope-api
```

If migration rollback is needed, decide case-by-case. Avoid destructive
migration rollback unless restore has been tested.

## 13. Launch Gate

Do not mark deployment successful until:

- [ ] all automated checks pass
- [ ] manual smoke passes
- [ ] logs show no startup exceptions
- [ ] monitoring receives metrics
- [ ] backup job succeeded
- [ ] OAuth works on deployed domain
- [ ] CORS blocks unknown origins

