# Scope: Stock Research Orchestrator

Scope is a production-oriented research pipeline for equity analysis across deterministic and LLM-powered stages:
query planning, web search, filtering, scraping, evidence extraction, and scoring.

The system has:

- FastAPI API app (`apps/api/src/scope_api/*`)
- Research domain package (`packages/research-core/src/research_core/*`)
- Provider integrations (`packages/provider-integrations/src/provider_integrations/*`)
- Shared API contracts (`packages/contracts/*`)
- Local persistence and artifact output (`storage/*`)
- Web UI (`apps/web/*`)
- Observability stack (`infra/observability/*`)

## Repo layout

Design reference:

- Figma/UI spec: [`docs/figma-spec.md`](docs/figma-spec.md)
- Editorial redesign spec: [`docs/editorial-ui-redesign-spec.md`](docs/editorial-ui-redesign-spec.md)
- Performance baseline: [`docs/performance-baseline.md`](docs/performance-baseline.md)
- Performance architecture guide: [`docs/performance-architecture-guide.md`](docs/performance-architecture-guide.md)
- Production readiness audit: [`docs/production-readiness-audit.md`](docs/production-readiness-audit.md)
- Security audit: [`docs/security-audit.md`](docs/security-audit.md)
- Deployment checklist: [`docs/deployment-checklist.md`](docs/deployment-checklist.md)
- Deployment automation and CI/CD: [`docs/deployment-automation-ci-cd.md`](docs/deployment-automation-ci-cd.md)
- Production smoke checklist: [`docs/production-smoke-checklist.md`](docs/production-smoke-checklist.md)
- Production refactor plan: [`docs/production-codebase-refactor-plan.md`](docs/production-codebase-refactor-plan.md)

### Backend And Packages

- `apps/api/src/scope_api/app.py` API entrypoint and endpoint definitions
- `apps/api/src/scope_api/application/run_service.py` orchestration and run lifecycle
- `packages/research-core/src/research_core/harness/*` Gemini-first research harness
- `packages/research-core/src/research_core/legacy_pipeline/main.py` deterministic fallback pipeline
- `packages/provider-integrations/src/provider_integrations/*` search, scrape, and provider adapters
- `packages/contracts/openapi/openapi.json` exported API contract
- `api_main.py` backend launch helper

### Frontend

- `apps/web/src/app/App.tsx` research UI shell and polling loop
- `apps/web/src/components/InitiateResearch.tsx` input and run trigger
- `apps/web/src/components/AnalysisDashboard.tsx` result rendering
- `apps/web/src/types/api.ts` typed API and result contracts
- `apps/web/vite.config.ts` local proxy for API calls

### Observability

- `infra/observability/docker-compose.yml`
- `infra/observability/prometheus.yml`
- `infra/observability/otel-collector-config.yaml`
- `infra/observability/tempo.yaml`
- `infra/observability/grafana/dashboards/scope-overview.json`
- `infra/observability/grafana/provisioning/dashboards/default.yml`
- `infra/observability/grafana/provisioning/datasources/prometheus-tempo.yml`
- `infra/observability/README.md`

## Features

- Async API with versioned routes under `/api/v1`
- Legacy compatibility routes (`/research-runs`) preserved
- Request correlation IDs and rate limiting middleware
- Deterministic + LLM hybrid pipeline
- API key auth optional via `RESEARCH_API_KEY`
- Durable DB-backed job queue with leases, heartbeats, and retry budget
- Event-driven worker wake-up via Postgres `LISTEN`/`NOTIFY` (no idle polling)
- Prometheus metrics and OpenTelemetry traces
- Grafana dashboard for production observability

## Prerequisites

- Python 3.13+
- Node.js 20+ (for the web app)
- Docker (for full observability stack)
- API keys for provider usage:
- `OPENAI_API_KEY`
- `EXA_API_KEY` (default search provider)
- Optional: `TAVI_API_KEY`/`TAVILY_API_KEY` and `SERPAPI_API_KEY`
- Optional for remote browser rendering fallback:
  - `CLOUDFLARE_ACCOUNT_ID` or `CF_ACCOUNT_ID`
  - `CLOUDFLARE_BROWSER_RENDERING_API_TOKEN` or `CF_BROWSER_RENDERING_API_TOKEN`

## Environment variables

### Backend

- `OPENAI_API_KEY`
- `EXA_API_KEY`
- `RESEARCH_API_KEY` (optional API key for endpoints)
- `RESEARCH_RATE_LIMIT_PER_MIN`
- `RESEARCH_RATE_LIMIT_BURST`
- `RESEARCH_API_MAX_LIMIT`
- `SEARCH_PROVIDER=auto|exa|tavily|google|ddg`
- `RESEARCH_MAX_RETRIES`
- `RESEARCH_LEASE_SECONDS` (worker lease duration, default 300)
- `RESEARCH_HEARTBEAT_SECONDS` (lease renewal interval, default 30)
- `RESEARCH_WORKER_POLL_SECONDS` (SQLite fallback only; Postgres uses LISTEN/NOTIFY)
- `SCOPE_ALLOWED_ORIGINS` (comma-separated CORS origins; required for a non-default UI port)
- `CLOUDFLARE_ACCOUNT_ID` or `CF_ACCOUNT_ID`
- `CLOUDFLARE_BROWSER_RENDERING_API_TOKEN` or `CF_BROWSER_RENDERING_API_TOKEN`
- `CLOUDFLARE_MARKDOWN_CACHE_TTL`
- `SCOPE_STORAGE_DIR` (default `storage`)
- `SCOPE_ARTIFACTS_DIR` (default `storage/artifacts`)
- `ARTIFACT_STORE_BACKEND=local|s3|minio` (default `local`)
- `ARTIFACT_BUCKET` (required for `s3`/`minio`)
- `ARTIFACT_PREFIX` (object-key prefix, default `scope`)
- `ARTIFACT_S3_ENDPOINT_URL` (MinIO/R2/B2/Wasabi endpoint)
- `ARTIFACT_S3_REGION` (default `us-east-1`)
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for S3-compatible storage
- `SCOPE_DB_BACKEND=sqlite|postgres` (default `sqlite`)
- `SCOPE_DB_PATH` (default `storage/research_runs.db`)
- `DATABASE_URL` (required when `SCOPE_DB_BACKEND=postgres`)
- `SCOPE_AUTO_MIGRATE=false|true` (run Alembic migrations on API startup when enabled)

### Observability

- `OBSERVABILITY_ENABLED=true|false`
- `OBSERVABILITY_METRICS_ENABLED=true|false`
- `OTEL_TRACES_ENABLED=true|false`
- `OTEL_SERVICE_NAME=scope-api`
- `OTEL_SERVICE_VERSION=0.1.0`
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`
- `OTEL_EXPORTER_OTLP_INSECURE=true`
- `OTEL_SAMPLING_RATIO=1.0`

Use `.env` to keep local defaults and values.

## Setup

### 1) Install backend dependencies

From project root — install third-party deps, then the four internal packages
as editable installs so their `src/` trees are importable:

```bash
uv sync
uv pip install --no-deps -e packages/agent-core -e packages/research-core -e packages/provider-integrations -e apps/api
```

Verify:

```bash
uv run python -c "import scope_api, research_core, agent_core, provider_integrations; print('ok')"
```

### 2) Start backend API

```bash
uv run python api_main.py
```

`api_main.py` runs database initialization, API middleware, and observability
wiring. **It does not execute research** — that is the worker's job.

### 3) Start the research worker

Research runs are executed by a separate worker process. Without it, submitted
runs stay `queued` forever:

```bash
uv run python -m scope_api.worker
```

The worker leases queued runs under a heartbeat-renewed lease. On Postgres it
waits on `LISTEN new_research_run` and wakes instantly; on local SQLite it falls
back to polling every `RESEARCH_WORKER_POLL_SECONDS`.

### 4) Start web app

```bash
cd apps/web
npm install
npm run dev
```

Frontend defaults:

- UI: `http://localhost:3000` (vite config port)
- API base: `http://localhost:8000` via `VITE_API_BASE_URL`

If you run the UI on a non-default port, add that origin to
`SCOPE_ALLOWED_ORIGINS` — the API sets `allow_credentials=True`, and browsers
reject a wildcard CORS origin on credentialed requests.

### 4) (Optional) Start full observability stack

```bash
cd infra/observability
docker compose up -d
```

Open:

- Grafana: `http://localhost:3000` (default `admin/admin`)
- Prometheus: `http://localhost:9090`
- Tempo API: `http://localhost:3200`
- Scope dashboard: `Scope Stock Research - Platform Observability`

### 5) Run local verification

```bash
uv run python -m pytest tests/test_*.py
uv run python scripts/export_openapi.py
uv run python scripts/validate_migrations.py
cd apps/web
npm run build
```

### Database migrations

Local development defaults to SQLite at `SCOPE_DB_PATH`. Production should use
Postgres with:

```bash
SCOPE_DB_BACKEND=postgres
DATABASE_URL=postgresql://user:password@host:5432/scope
uv run python scripts/migrate_db.py
uv run python scripts/validate_migrations.py
```

`validate_migrations.py` upgrades the configured database and verifies the
tables and indexes Scope needs for users, research runs, artifacts, memory, and
advisor runs. Run it against a disposable Postgres database before VPS deploys.

## API contract summary

### Legacy routes used by UI

- `POST /research-runs`
- `GET /research-runs/{run_id}`
- `GET /research-runs/{run_id}/results`

Payload on create:

```json
{
  "company_name": "Acme Corp",
  "ticker": "ACME"
}
```

### Versioned routes

- `POST /api/v1/research-runs`
- `GET /api/v1/research-runs`
- `GET /api/v1/research-runs/{run_id}`
- `GET /api/v1/research-runs/{run_id}/results`
- `GET /api/v1/health`

Both layers return run status/progress and result payloads required by the UI.

## UI behavior

- Start triggers `POST /research-runs`
- UI polls `/research-runs/{run_id}` until `completed` or `failed`
- On completion it calls `/research-runs/{run_id}/results`
- Dashboard displays:
  - summary
  - scorecard
  - pillar assessments
  - source links and evidence

## Development notes

- Web UI is currently built against legacy endpoints (`/research-runs*`) by design.
- Old `src.*` imports are compatibility shims during the monorepo migration.
- Canonical backend imports are `scope_api`, `research_core`, and `provider_integrations`.
- Observability is implemented end-to-end in backend layers and exposed via:
  - API metrics at `/metrics`
  - traces through OTLP to Grafana Tempo
- Frontend does not currently request `/metrics` directly.

## Security and operations notes

- Do not commit real secrets in `README.md` or `apps/web` source.
- Consider:
  - rotating keys regularly
  - moving secrets to a secret manager
  - replacing in-process orchestrator with durable queue in production
  - enabling HTTPS and proper API key/tenant auth for user-facing deployments

## Data persistence

- Runs and status are stored in `storage/research_runs.db` during local execution by default.
- Stage artifacts and intermediate outputs are written under `storage/artifacts/` by default.
- Existing historical `artifacts/` data is left untouched for compatibility and audit.
- For production, migrate persistence to a managed SQL store and object storage as planned in design.

## Troubleshooting

- If web creates runs but they stay `queued`:
  - confirm the worker process is running (`uv run python -m scope_api.worker`) —
    this is the most common cause, since the API never executes research itself
  - verify backend health at `http://localhost:8000/health`; the `orchestrator`
    field reports current `queued` / `running` counts
  - check `RESEARCH_*` limits are not rejecting submissions
- If the UI loads but stays on "Loading your workspace…":
  - check the browser console for a CORS error and add the UI origin to
    `SCOPE_ALLOWED_ORIGINS`
- If dashboards stay empty:
  - confirm API exposes `/metrics`
  - confirm `OBSERVABILITY_*` flags are enabled
  - confirm stack services are running and the API is reaching OTLP endpoint
- If search calls fail:
  - verify `SEARCH_PROVIDER` and provider key settings
  - test with `auto` to allow fallback provider chain

## License

This repository is currently using a local/internal license setup and is not yet formalized.
