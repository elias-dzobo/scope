# Scope Codebase Structure

This document defines the target repository structure for separating the product UI from the backend research platform while keeping local development simple.

## Current Problem

The repo now has deployable app and package boundaries in place, with temporary `src.*` compatibility shims for the migration:

- backend API lives under `apps/api/src/scope_api`
- frontend app lives under `apps/web`
- research domain logic lives under `packages/research-core/src/research_core`
- provider adapters live under `packages/provider-integrations/src/provider_integrations`
- shared contracts live under `packages/contracts`
- legacy generated frontend outputs may still exist under `web/dist/` or `web/artifacts/` until storage cleanup lands
- new runtime artifacts default to `storage/artifacts`
- old `src.*` imports are compatibility shims and should not be used in new code

The target structure should make each layer independently understandable, testable, and deployable.

## Target Structure

```text
scope/
  apps/
    api/
      src/
        scope_api/
          main.py
          routes/
          schemas/
          middleware/
          dependencies.py
      tests/
      pyproject.toml
      README.md

    web/
      src/
        app/
        components/
        features/
          research-runs/
          research-report/
          archive/
        lib/
          api/
          formatting/
        types/
      public/
      tests/
      journeys/
      package.json
      vite.config.ts
      README.md

  packages/
    research-core/
      src/
        research_core/
          framework/
          harness/
          documents/
          grounding/
          evidence/
          scoring/
          reports/
      tests/
      pyproject.toml

    provider-integrations/
      src/
        provider_integrations/
          search/
          llm/
          browser_rendering/
      tests/
      pyproject.toml

    contracts/
      openapi/
      generated/
        python/
        typescript/
      README.md

  infra/
    observability/
    docker/
    deployment/

  docs/
    architecture/
    product/
    operations/

  storage/
    artifacts/
    runs.db

  scripts/
  notebooks/
  .env.example
  README.md
```

## Ownership Boundaries

### `apps/api`

Owns HTTP concerns only:

- FastAPI app creation
- routes
- request/response schemas
- auth, rate limits, middleware
- run submission and polling endpoints
- API-level dependency wiring

It should not own research logic, scoring, document parsing, or provider-specific code.

### `apps/web`

Owns the frontend product:

- React app shell
- research creation flow
- live progress UI
- report rendering
- archive/history screens
- browser journeys and UI tests
- frontend-only formatting and API client code

It should talk to the backend through typed API clients generated from contracts or maintained in `apps/web/src/lib/api`.

### `packages/research-core`

Owns domain and agentic research behavior:

- six-pillar framework definitions
- `ResearchBrief`, `ResearchPlan`, `ResearchWorkstream`
- Gemini-first harness controller and runners
- grounding result normalization
- primary-document acquisition model
- PDF/table parsing abstractions
- evidence extraction and alignment
- scoring and recommendation logic
- report assembly

This package should be usable without FastAPI or React.

### `packages/provider-integrations`

Owns adapters to external systems:

- Exa, Tavily, SerpAPI, DuckDuckGo
- Gemini/OpenAI model calls
- Cloudflare browser rendering
- Selenium/browser fallback

Research-core calls provider interfaces; provider-integrations implements them.

### `packages/contracts`

Owns public contracts shared across frontend and backend:

- OpenAPI schema
- generated TypeScript API types
- generated Python client/types if needed
- compatibility notes for legacy routes

This prevents drift between `apps/api/src/scope_api/schemas.py` and `apps/web/src/types/api.ts`.

### `storage`

Owns local runtime state only:

- generated research artifacts
- parsed documents
- local SQLite database
- run outputs

Anything under `storage/` should be considered disposable local runtime data unless explicitly exported.

## Near-Term Migration From Current Repo

The refactor should happen in safe phases.

### Phase 1: Introduce App Boundaries Without Moving Everything

Status: complete for the current Vite app.

Create target folders and move only frontend source into a clearer `apps/web` shape:

```text
web/App.tsx                  -> apps/web/src/app/App.tsx
web/index.tsx                -> apps/web/src/app/main.tsx
web/components/*             -> apps/web/src/components/*
web/types.ts                 -> apps/web/src/types/api.ts
web/journeys/*               -> apps/web/journeys/*
web/horus.config.yaml        -> apps/web/horus.config.yaml
```

Keep Vite working after path updates.

### Phase 2: Move Backend App Shell

Status: complete with compatibility shims.

Move API transport code into `apps/api`, but keep imports compatible:

```text
api_main.py                  -> apps/api/main.py
src/api/*                    -> apps/api/src/scope_api/*
src/application/*            -> apps/api/src/scope_api/application/*
```

During this phase, `scope_api` may still import from the existing root `src` domain modules.

### Phase 3: Extract Research Core

Status: complete with compatibility shims.

Move domain logic out of backend-shaped `src/`:

```text
src/harness/*                -> packages/research-core/src/research_core/harness/*
src/agent/main.py            -> packages/research-core/src/research_core/scoring/*
src/schema/tool_schema.py    -> packages/research-core/src/research_core/schemas/*
src/prompts/tool_prompts.py  -> packages/research-core/src/research_core/prompts/*
src/pipeline/main.py         -> packages/research-core/src/research_core/legacy_pipeline/*
```

The old deterministic pipeline should become `legacy_pipeline` while the Gemini-first harness becomes the primary core path.

### Phase 4: Extract Provider Integrations

Status: complete with compatibility shims.

Move external adapters out of research core:

```text
src/integrations/search.py   -> packages/provider-integrations/src/provider_integrations/search/*
src/tools/main.py            -> split between provider_integrations and research_core legacy tools
```

This lets us test domain logic with fake providers.

### Phase 5: Contracts And Generated Types

Status: OpenAPI export is in place. TypeScript generation is reserved for the next pass.

Add OpenAPI export and generated TypeScript types. The frontend should stop manually drifting from backend response schemas.

### Phase 6: Runtime Storage Defaults

Status: complete for local defaults.

New writes default to `storage/artifacts` and `storage/research_runs.db`, with `SCOPE_ARTIFACTS_DIR` and `SCOPE_DB_PATH` available for overrides.

## Naming Rules

- Use `apps/*` for deployable products.
- Use `packages/*` for reusable libraries.
- Use `storage/*` for generated local runtime data.
- Avoid generic module names like `tools`, `utils`, `agent`, and `main` in new code.
- Prefer domain names: `harness`, `grounding`, `documents`, `evidence`, `scoring`, `research_runs`, `provider_integrations`.
- Keep generated files out of source directories.

## Deployment Shape

The target structure supports separate deploys:

```text
apps/api  -> backend service
apps/web  -> static frontend / Vercel / Netlify / CDN
storage   -> local only, replaced by DB + object storage in production
infra     -> observability and deployment config
```

For local development, the web app should continue to proxy API calls to `http://localhost:8000`.

## Immediate Recommendation

New code should import canonical packages directly: `scope_api`, `research_core`, and `provider_integrations`. Keep the `src.*` shims until downstream callers have migrated and CI is green without legacy imports.
