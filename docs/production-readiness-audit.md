# Production Readiness Audit

Date: 2026-05-12

This audit reviews Scope as it stands before production deployment. It focuses
on runtime reliability, operational maturity, data durability, observability,
performance, and release safety.

## Executive Summary

Scope is close to a beta-grade production deployment, but it is not yet ready
for open public traffic. The app has the right foundations: FastAPI, Postgres
migrations, MinIO-compatible artifact storage, Google auth, advisor
conversations, GraphRAG memory, tests, OpenAPI export, and a Vite frontend.

The main production gaps are:

- API CORS is currently wildcard.
- Research routes still allow anonymous ownership paths.
- Rate limiting is process-local.
- Worker orchestration is in-process.
- Secrets/config validation is too loose.
- Generated/local artifacts still exist in repo-adjacent directories.
- Monitoring exists, but production alerting and runbooks are not complete.
- Legacy compatibility modules still obscure ownership boundaries.

## Readiness Rating

Current stage: **private beta only**

Allowed traffic:

- trusted internal users
- low concurrency
- manual operational oversight
- no paid users yet

Not yet recommended:

- public launch
- high-concurrency research workloads
- regulated financial-advice positioning
- multi-region or autoscaled deployment

## P0 Before Any Real Users

### 1. Lock Down CORS

Current risk:

`apps/api/src/scope_api/app.py` uses:

```python
allow_origins=["*"]
allow_methods=["*"]
allow_headers=["*"]
```

Production requirement:

- Add `SCOPE_ALLOWED_ORIGINS`.
- Only allow the deployed frontend domain.
- Keep local defaults for development.

Acceptance:

- Production API rejects browser requests from unknown origins.
- Local development still works with `localhost`.

### 2. Require Auth For User Research

Current risk:

Research routes support anonymous runs for legacy compatibility. This makes
sense locally, but production should require authenticated ownership for:

- starting research
- listing runs
- reading results
- reading artifacts
- advisor conversations
- memory routes

Production requirement:

- Add `SCOPE_REQUIRE_AUTH=true` in production.
- Keep optional auth only when explicitly disabled for local development.

Acceptance:

- Anonymous production requests to user-owned data routes return `401`.
- Existing local tests can still run with compatibility mode.

### 3. Move Rate Limiting Out Of Process

Current risk:

The rate limiter is in memory. It works for one process, but not across:

- multiple uvicorn workers
- restarts
- horizontal scaling

Production requirement:

- For first VPS deployment, use Nginx/Caddy rate limits as an edge guard.
- For scaled API deployment, move app rate limits to Redis.

Acceptance:

- Per-IP and per-user limits enforced at edge.
- Expensive research creation has stricter limits than read endpoints.

### 4. Separate Web And API Domains

Recommended:

```text
app.yourdomain.com      frontend
api.yourdomain.com      backend
```

Acceptance:

- Google OAuth authorized origins include frontend domain.
- Google OAuth redirect/client config matches frontend env.
- API CORS allows frontend domain only.

### 5. Production Database And Object Storage

Production target:

- Postgres for relational state
- MinIO for artifacts

Required env:

```bash
SCOPE_DB_BACKEND=postgres
DATABASE_URL=postgresql://...
ARTIFACT_STORE_BACKEND=minio
ARTIFACT_BUCKET=scope-artifacts
ARTIFACT_S3_ENDPOINT_URL=http://127.0.0.1:9000
```

Acceptance:

```bash
uv run python scripts/migrate_db.py
uv run python scripts/validate_migrations.py
```

passes against production-like Postgres.

### 6. Worker Process Strategy

Current risk:

Research orchestration runs inside the API process. This is okay for private
beta, but not ideal for production stability.

Private beta requirement:

- Run one API process with conservative `RESEARCH_MAX_WORKERS`.
- Keep request timeout high enough for creation/status endpoints, but do not
run long research in request handlers.
- Monitor queue depth and failed runs.

Next production requirement:

- Extract workers into a separate process:

```text
scope-api        FastAPI routes
scope-worker     research queue execution
postgres         durable run state
minio            artifacts
```

## P1 Before Paid Beta

### 1. Background Job Durability

Current risk:

If the API process dies, in-flight in-process work can be lost or left in an
ambiguous state.

Recommended path:

- Add durable job leasing in Postgres.
- Add `queued`, `leased`, `heartbeat`, `retry_count`, and `lease_expires_at`.
- Worker resumes queued/stale runs after restart.

### 2. Observability And Alerts

Existing:

- Prometheus metrics
- OpenTelemetry hooks
- Grafana dashboard config

Add:

- alert for API 5xx rate
- alert for failed research rate
- alert for queue depth
- alert for worker heartbeat absence
- alert for Postgres disk utilization
- alert for MinIO disk utilization
- alert for provider API error spikes

### 3. Cost Controls

Research can trigger expensive external calls.

Add:

- per-user daily research limit
- per-user advisor deep research limit
- provider call budget per run
- LLM token budget per run
- artifact size cap
- maximum document count per run

### 4. Data Retention Policy

Define:

- how long raw artifacts are kept
- how users delete research
- how users delete their account
- whether memory graph nodes are deleted or tombstoned
- backup retention period

### 5. Backup And Restore

Required:

- daily Postgres dump
- MinIO bucket backup/snapshot
- restore rehearsal against staging

Acceptance:

- restore test can recreate a working user, research run, artifacts, advisor
  conversation, and memory graph from backup.

## P2 Hardening

- Add staging environment.
- Add CI deploy gates.
- Add dependency vulnerability scanning.
- Add Playwright smoke test for login, onboarding, research library, advisor.
- Add structured release notes.
- Add blue/green or rolling deploy plan.

## Production Go/No-Go

Do not deploy to public users until these are true:

- CORS restricted.
- Auth required for user-owned routes.
- Postgres migrations validated.
- MinIO configured and private.
- Backups configured.
- OAuth production credentials configured.
- TLS enabled.
- Secrets stored outside repo.
- Observability stack running.
- Full backend tests pass.
- Web build passes.
- OpenAPI export passes.

## Verification Commands

```bash
uv run python -m pytest tests/test_*.py -q
uv run python scripts/validate_migrations.py
uv run python scripts/export_openapi.py
cd apps/web && npm run build
```

