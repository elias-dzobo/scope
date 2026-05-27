# Production Codebase Refactor Plan

This plan moves Scope from a compatibility-first monorepo into a cleaner
production structure. The goal is not cosmetic; it is to make ownership,
deployment, testing, security review, and scaling easier.

## Current State

The repo has a good target shape:

```text
apps/
  api/
  web/
packages/
  research-core/
  provider-integrations/
  contracts/
infra/
docs/
scripts/
storage/
```

But it still carries transitional and generated areas:

```text
src/                         compatibility shims and legacy modules
research_core/               root import shim
provider_integrations/       root import shim
scope_api/                   root import shim
web/                         old generated/frontend outputs
artifacts/                   old local runtime artifacts
apps/web/dist/               generated build output
apps/web/node_modules/       local dependency output
storage/                     local runtime state
brain/                       planning scratchpad
notebook/                    exploratory work
data.json                    unclear ownership
main.py                      legacy entrypoint
```

## Target Production Structure

```text
apps/
  api/
    src/scope_api/
    README.md
    systemd/
  web/
    src/
    README.md
packages/
  research-core/
    src/research_core/
  provider-integrations/
    src/provider_integrations/
  contracts/
    openapi/
    generated/
infra/
  deploy/
    caddy/
    nginx/
    systemd/
    docker-compose.production.yml
  observability/
docs/
scripts/
tests/
evaluations/
```

Runtime-only paths should not live in git:

```text
storage/
artifacts/
web/artifacts/
apps/web/dist/
apps/web/node_modules/
```

## Refactor Principles

- Keep production deploy paths boring and explicit.
- Remove root ambiguity.
- Keep compatibility shims until tests and deploy scripts no longer need them.
- Delete generated/local artifacts from git only after confirming `.gitignore`.
- Do not mix runtime data with source code.
- Keep `apps/api` deployable without importing from legacy `src`.
- Keep `apps/web` deployable without root web artifacts.

## Phase 1: Production Boundary Cleanup

Priority: P0

Tasks:

- Confirm all API runtime imports use:
  - `scope_api`
  - `research_core`
  - `provider_integrations`
- Add tests that fail if production code imports from `src.*`.
- Keep `src.*` only for compatibility tests and old scripts.
- Mark `main.py` as deprecated or remove if unused.
- Confirm `api_main.py` is the only backend launch helper.

Acceptance:

```bash
uv run python -m pytest tests/test_refactor_boundaries.py -q
```

passes and no production module imports legacy root code.

## Phase 2: Runtime Artifact Cleanup

Priority: P0 before deploy

Tasks:

- Keep `.gitignore` entries for:
  - `storage/`
  - `artifacts/`
  - `web/artifacts/`
  - `apps/web/dist/`
  - `apps/web/node_modules/`
- Remove generated/local runtime files from version control if tracked.
- Keep migration scripts and fixture files.
- Document local artifact migration from old `artifacts/` to `storage/artifacts/`.

Acceptance:

```bash
git status --ignored
```

shows runtime files ignored.

## Phase 3: API App Packaging

Priority: P1

Tasks:

- Add `apps/api/README.md`.
- Add production process examples under `infra/deploy/systemd/`.
- Move API-specific deployment config out of root docs where practical.
- Add `SCOPE_ENV=production` startup validation.
- Add config validation module:
  - database backend
  - CORS origins
  - JWT secret strength
  - dev auth disabled
  - artifact backend requirements

Acceptance:

- Production boot fails fast on unsafe env.
- Local boot remains easy.

## Phase 4: Worker Extraction

Priority: P1 before paid beta

Current:

API startup starts orchestrator workers.

Target:

```text
scope-api       serves HTTP only
scope-worker    runs research jobs
```

Tasks:

- Add `apps/api/src/scope_api/worker.py`.
- Move run execution loop out of API startup.
- Add DB-backed job leasing.
- Add stale lease recovery.
- Add worker heartbeat.
- Add worker-specific systemd service.

Acceptance:

- API restart does not interrupt queued work.
- Worker restart can resume stale queued/leased runs.

## Phase 5: Contracts And Frontend Types

Priority: P1

Tasks:

- Keep `scripts/export_openapi.py`.
- Generate TypeScript contracts into `packages/contracts/generated/typescript`.
- Move `apps/web/src/types/api.ts` toward generated types.
- Keep handwritten UI-only types separate.

Acceptance:

- OpenAPI export includes advisor conversation routes.
- Frontend build uses contract-generated API shapes.

## Phase 6: Provider Boundary

Priority: P1/P2

Tasks:

- Ensure `research_core` depends on protocols/interfaces, not concrete provider clients.
- Keep `provider_integrations` responsible for:
  - OpenAI/Gemini wrappers
  - search providers
  - browser rendering
  - scraping fallbacks
- Move remaining provider-specific code out of `research_core` where practical.

Acceptance:

- Core harness can be tested with fake providers.
- Provider integration tests can run separately.

## Phase 7: Security-Oriented Config

Priority: P0/P1

Tasks:

- Add `SCOPE_ALLOWED_ORIGINS`.
- Add `SCOPE_REQUIRE_AUTH`.
- Add production startup validation.
- Add secure cookie/session migration plan.
- Add Nginx/Caddy deploy config with headers and body limits.

Acceptance:

- Unsafe production config fails startup.
- Security checklist can be verified automatically where possible.

## Phase 8: Test And CI Structure

Priority: P1

Tasks:

- Split tests by concern:
  - `tests/api/`
  - `tests/research_core/`
  - `tests/provider_integrations/`
  - `tests/frontend_contracts/`
- Keep broad `tests/test_*.py` compatibility until split is complete.
- Add CI jobs:
  - backend tests
  - migration validation
  - OpenAPI export
  - web build
  - advisor evals

Acceptance:

```bash
uv run python -m pytest tests/test_*.py -q
uv run python scripts/validate_migrations.py
uv run python scripts/export_openapi.py
cd apps/web && npm run build
```

all pass in CI.

## Phase 9: Remove Compatibility Shims

Priority: P2, only after stable deploy

Tasks:

- Remove or archive:
  - `src/api`
  - `src/application`
  - `src/harness`
  - `src/pipeline`
  - `src/schema`
  - root import shims if no longer needed
- Update all scripts/tests/docs.

Acceptance:

- No imports from `src.*`.
- CI green.
- Production deploy green.

## Production Refactor Go/No-Go

Do not remove compatibility shims until:

- production deploy uses canonical paths
- tests verify canonical imports
- OpenAPI export is stable
- frontend builds from `apps/web`
- no runtime data is tracked
- rollback path is documented

