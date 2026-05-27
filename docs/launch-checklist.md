# Launch Checklist

Deployment target: **Fly.io** (API + worker) + **Neon Postgres** + **Tigris / Cloudflare R2** (artifacts).

VPS deployment docs are preserved in `docs/vps-deployment-guide.md` and `docs/deployment-checklist.md`
for when we scale beyond Fly.io.

---

## Part 1 — Security & Secrets

- [ ] **Revoke every key currently in `.env`** — OpenAI, EXA, Tavily, Gemini, Google OAuth client secret. Rotate them all before touching production.
- [ ] Generate fresh keys scoped per environment (dev / prod). Never share keys across environments.
- [ ] Remove `.env` from the repo entirely or confirm it is in `.gitignore` and contains only placeholder values.
- [ ] Set all production secrets via `fly secrets set` — never commit them or put them in `fly.toml`.
- [ ] Confirm `AUTH_ALLOW_DEV_GOOGLE_TOKEN=false` is set in production secrets.
- [ ] Confirm `SCOPE_REQUIRE_AUTH=true` is set.
- [ ] Confirm `SCOPE_ALLOWED_ORIGINS` is set to your exact frontend domain (no wildcard).
- [ ] Generate a `JWT_SECRET` of 32+ random characters.
- [ ] Run `scripts/check_production_config.py` — production boot must fail if any of the above are missing or unsafe.

---

## Part 2 — Fly.io Setup

- [ ] Copy `infra/fly/fly.toml.example` to `fly.toml` and fill in app name, region, and process groups.
- [ ] Create the Fly app: `fly apps create <app-name>`.
- [ ] Configure the two process groups in `fly.toml`:
  - `web` — serves HTTP, scales on request load.
  - `worker` — no public HTTP service, leases jobs from Postgres.
- [ ] Set `RESEARCH_EXECUTION_BACKEND=durable` so the API only queues jobs; the worker executes them.
- [ ] Set minimum machines: `fly scale count web=1 worker=1`.
- [ ] Confirm `fly.toml` has a `release_command` that runs migrations before new machines start:
  ```
  release_command = "uv run python scripts/migrate_db.py"
  ```
- [ ] Confirm health check is configured in `fly.toml` pointing at `/api/v1/health`.
- [ ] Confirm `SCOPE_ENV=production` and `SCOPE_DB_BACKEND=postgres` are in fly secrets.

---

## Part 3 — Neon Postgres

- [ ] Create a Neon project and database.
- [ ] Create a least-privilege app role (not the project owner role) before public launch.
- [ ] Set the connection string as a Fly secret:
  ```bash
  fly secrets set DATABASE_URL='postgresql://USER:PASSWORD@HOST/neondb?sslmode=require'
  ```
- [ ] Use the **pooled** connection string for API runtime traffic.
- [ ] Use the **direct** connection string for migrations if the pooler does not support migration DDL.
- [ ] Rotate the Neon password if it has ever appeared in chat, logs, or the repo.
- [ ] Verify `sslmode=require` is in the connection string.
- [ ] Run migrations manually against Neon before first deploy and confirm with `scripts/validate_migrations.py`.
- [ ] Enable point-in-time restore on the Neon plan you are using.
- [ ] Confirm Neon automated backups are active.

---

## Part 4 — Object Storage (Artifacts)

- [ ] Choose a provider: Tigris (Fly native) or Cloudflare R2.
- [ ] Create a bucket and generate access credentials.
- [ ] Set artifact secrets:
  ```bash
  fly secrets set \
    ARTIFACT_STORE_BACKEND=s3 \
    ARTIFACT_BUCKET=scope-artifacts \
    ARTIFACT_S3_ENDPOINT_URL=<provider endpoint> \
    AWS_ACCESS_KEY_ID=<key> \
    AWS_SECRET_ACCESS_KEY=<secret>
  ```
- [ ] Confirm bucket is **not** publicly accessible.
- [ ] Set artifact retention policy in secrets:
  ```bash
  ARTIFACT_RETENTION_MODE=ephemeral
  ARTIFACT_KEEP_TYPES=final_synthesis,document_evidence
  ARTIFACT_TEMP_TTL_HOURS=72
  ARTIFACT_KEEP_FAILED_RUNS_DAYS=14
  ARTIFACT_KEEP_RAW_DOCUMENTS=false
  ```
- [ ] Run artifact store smoke test:
  ```bash
  ARTIFACT_STORE_BACKEND=s3 uv run python -m pytest tests/test_artifact_store.py -q
  ```

---

## Part 5 — Frontend & DNS

- [ ] Register your domain and point DNS at Fly (`CNAME` to `<app-name>.fly.dev` or `A` to Fly IPs).
- [ ] Configure Google OAuth:
  - Authorized JavaScript origin: `https://yourdomain.com`
  - Authorized redirect URI: `https://yourdomain.com/auth/callback`
- [ ] Set `VITE_API_BASE_URL` in the web build to the production API URL.
- [ ] Set `VITE_GOOGLE_CLIENT_ID` to the production OAuth client ID.
- [ ] Build the frontend and confirm it bundles cleanly:
  ```bash
  cd apps/web && npm ci && npm run build
  ```
- [ ] Confirm Fly serves static assets from `apps/web/dist` or deploy frontend to a CDN/static host.
- [ ] Set `SCOPE_ALLOWED_ORIGINS` to match the deployed frontend domain exactly.

---

## Part 6 — Pre-Deploy Verification

Run all of these locally against a production-like config before `fly deploy`:

- [ ] `uv run python -m pytest tests/test_*.py -q` — full test suite passes.
- [ ] `uv run python scripts/export_openapi.py` — OpenAPI export succeeds.
- [ ] `uv run python scripts/validate_migrations.py` — schema validates against expected tables and indexes.
- [ ] `cd apps/web && npm run build` — frontend build passes.
- [ ] `pip-audit` and `npm audit` — no unresolved critical CVEs.

---

## Part 7 — First Deploy & Smoke Test

- [ ] Deploy: `fly deploy`.
- [ ] Confirm both `web` and `worker` machines start cleanly: `fly status`.
- [ ] Check logs for startup exceptions: `fly logs`.
- [ ] Hit health endpoints:
  ```bash
  curl https://yourdomain.com/health
  curl https://yourdomain.com/api/v1/health
  ```
- [ ] Manual smoke test:
  - [ ] Landing page loads.
  - [ ] Google sign-in works on the production domain.
  - [ ] Onboarding save works.
  - [ ] Start a research run — run enters queue.
  - [ ] Run completes and results render.
  - [ ] Advisor conversation opens on a completed run.
  - [ ] Advisor follow-up stays in thread context.
  - [ ] Saved research library loads.
  - [ ] Logout clears session.
- [ ] Confirm CORS blocks a request from an unknown origin.
- [ ] Confirm an unauthenticated request to a research endpoint is rejected.

---

## Part 8 — Observability

- [ ] Set `OBSERVABILITY_ENABLED=true` in fly secrets.
- [ ] Confirm Prometheus metrics are reachable at `/metrics`.
- [ ] Confirm traces appear in Grafana / Tempo.
- [ ] Configure Prometheus alerting rules for:
  - [ ] API 5xx spike.
  - [ ] Research run failure rate above threshold.
  - [ ] Worker heartbeat missing.
  - [ ] Queue depth exceeding limit.
- [ ] Set up alert delivery (email, Slack, or PagerDuty).

---

## Part 9 — Cost Controls

- [ ] Set provider budgets in fly secrets:
  ```bash
  RESEARCH_PROVIDER_CALL_BUDGET=80
  RESEARCH_TOKEN_BUDGET=150000
  RESEARCH_RATE_LIMIT_PER_MIN=30
  RESEARCH_RATE_LIMIT_BURST=10
  RESEARCH_MAX_ACTIVE_RUNS_PER_USER=2
  ```
- [ ] Use separate API keys per environment so dev usage does not bleed into prod budgets.
- [ ] Set provider-level spend limits in the OpenAI / Gemini / Exa dashboards.

---

## Part 10 — Launch Gate

Do not open to users until all of the following are true:

- [ ] All secrets rotated, none committed to the repo.
- [ ] Production config validation passes at boot.
- [ ] Full test suite green against production Postgres.
- [ ] Manual smoke test passed on the deployed domain.
- [ ] Logs show no startup exceptions.
- [ ] Observability is receiving metrics and traces.
- [ ] Neon PITR confirmed active.
- [ ] Artifact bucket confirmed private.
- [ ] CORS and auth checks confirmed working.
- [ ] Cost guardrails set.

---

---

# Codebase Structure Refactor Checklist

Full plan is in `docs/production-codebase-refactor-plan.md`. This checklist tracks the actionable items in order.

## Phase 1 — Production Import Boundary (P0)

- [ ] Audit all production modules in `apps/api/src/scope_api/` and confirm zero imports from `src.*`.
- [ ] Audit `packages/research-core/` and confirm zero imports from `src.*`.
- [ ] Audit `packages/provider-integrations/` and confirm zero imports from `src.*`.
- [ ] Write or enable `tests/test_refactor_boundaries.py` — a test that fails if any production module imports from legacy `src.*`.
- [ ] Confirm `api_main.py` is the only backend launch entrypoint; mark or remove `main.py`.

## Phase 2 — Runtime Artifact Cleanup (P0 before deploy)

- [ ] Confirm `storage/`, `artifacts/`, `web/artifacts/`, `apps/web/dist/`, `apps/web/node_modules/` are all in `.gitignore`.
- [ ] Remove any tracked runtime files from git:
  ```bash
  git rm -r --cached storage/ artifacts/ apps/web/dist/ apps/web/node_modules/
  ```
- [ ] Confirm migration scripts and fixture files are still tracked.
- [ ] Confirm `git status --ignored` shows runtime paths as ignored.

## Phase 3 — Root-Level Shim Cleanup (P1)

- [ ] Identify all imports of root-level shims: `research_core/`, `provider_integrations/`, `scope_api/` at root.
- [ ] Confirm these shims are only used in legacy scripts or compatibility tests — not in the production code path.
- [ ] Add a note in each shim file marking it as a compatibility shim to be removed after Phase 9.

## Phase 4 — Worker Extraction (P1 before paid beta)

- [ ] Confirm `apps/api/src/scope_api/worker.py` is the durable worker entrypoint.
- [ ] Confirm API startup does **not** start research execution inline (orchestration.py should not run in production).
- [ ] Confirm DB-backed job leasing is in place (migration `0007_durable_research_jobs`).
- [ ] Confirm stale lease recovery works: kill a worker mid-run, restart, confirm run resumes.
- [ ] Confirm worker heartbeat is recorded in the database.
- [ ] Verify `fly.toml` runs `worker` as a separate process group from `web`.

## Phase 5 — Frontend Type Contracts (P1)

- [ ] Confirm `scripts/export_openapi.py` exports all API routes including advisor conversation endpoints.
- [ ] Generate TypeScript types from the OpenAPI spec into `packages/contracts/generated/typescript/`.
- [ ] Migrate `apps/web/src/types/api.ts` to use the generated types where practical.
- [ ] Keep handwritten UI-only types separate from the generated contract types.

## Phase 6 — Provider Boundary (P1/P2)

- [ ] Confirm `research_core` depends on provider protocols/interfaces, not concrete client classes.
- [ ] Move any provider-specific client code still inside `research_core` into `provider_integrations`.
- [ ] Confirm harness tests can run with fake/stub providers without real API keys.
- [ ] Confirm provider integration tests can run independently of core pipeline tests.

## Phase 7 — Test Structure (P1)

- [ ] Split tests into subdirectories:
  - `tests/api/`
  - `tests/research_core/`
  - `tests/provider_integrations/`
  - `tests/frontend_contracts/`
- [ ] Keep existing `tests/test_*.py` files working until the split is complete.
- [ ] Update `pytest.ini` or `pyproject.toml` test paths after the split.
- [ ] Confirm CI runs all test groups.

## Phase 8 — Brain / Notebook Cleanup (P2)

- [ ] Review `brain/` — move any canonical docs or decisions into `docs/`, delete the rest.
- [ ] Review `notebook/` — archive or delete exploratory notebooks not used in production.
- [ ] Review `data.json` — determine ownership and move or delete.

## Phase 9 — Remove Compatibility Shims (P2, after stable deploy)

**Do not do this until:** production is deployed and stable, all tests use canonical imports, and a rollback path is documented.

- [ ] Remove `src/api`, `src/application`, `src/harness`, `src/pipeline`, `src/schema`.
- [ ] Remove root-level import shims (`research_core/`, `provider_integrations/`, `scope_api/` at root) if no longer needed.
- [ ] Update all scripts, tests, and docs to canonical import paths.
- [ ] Confirm CI is green after removal.
- [ ] Confirm production deploy is green after removal.
