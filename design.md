# Scope: Production-Grade System Design

This document captures the target architecture for a production-ready version of the project, organized by layer. For each layer, it states:
- Design decision(s)
- Why this was chosen
- Tradeoffs
- Expected scale effect
- Standard production best practices to follow

The design aligns with the current codebase structure:
- FastAPI API layer (`src/api/*`)
- Pipeline and orchestration (`src/pipeline/main.py`, `src/api/service.py`)
- Tools/data/reasoning (`src/tools/*`, `src/agent/*`, `src/utils/*`)
- Persistence (`src/api/db.py`)
- Provider integrations (search/LLM/scraping)

---

## 1) Client/API Layer

### Decision
- Keep FastAPI as the public contract surface and split endpoints by version (`/api/v1/...`) and resource semantics.
- Make API endpoints asynchronous, idempotent where possible, and thin (no heavy business logic).
- Push auth, validation, rate limiting, tracing, and response shaping into middleware/dependencies.
- Return structured DTOs only from response schemas.

### Why
- Existing API already has a clear request/response boundary (`src/api/schemas.py`) and a clean external contract.
- Async endpoints improve throughput under mixed IO-bound workloads.
- Versioning avoids breaking downstream consumers as recommendation payloads evolve.

### Tradeoffs
- Additional complexity in backward-compatibility strategy (multiple schema versions, migration window).
- Need stronger API governance (deprecations, changelog, contracts).
- Overhead of richer middleware stack (slight latency and engineering effort).

### Scale effect
- With async + clean serialization, this layer scales mainly with CPU-bound response formatting and middleware overhead.
- Under concurrent clients, the API server remains stable when most work is moved to async queue workers.
- Throughput scales horizontally by adding FastAPI replicas behind a load balancer.

### Production best practices
- Enforce strict request/response schemas (`pydantic v2`) and OpenAPI contracts.
- Add input bounds (max payload size, query/page limits), auth with least privilege, and per-tenant quotas.
- Emit correlation IDs, request IDs, and structured logs.
- Add rate limiting keyed by tenant/API key with burst + refill policies.
- Add health/readiness endpoints that verify DB, queue, and critical dependency dependency connectivity.

---

## 2) Orchestration / Workflow Layer

### Decision
- Replace `threading.Thread`-based execution in `src/api/service.py` with a durable workflow engine.
  - Option A: Celery/Redis (MVP, simpler operations)
  - Option B: Temporal (stronger saga/state durability and visibility)
- Represent the stock research process as an explicit stage graph (DAG/state machine):
  `01_prepare -> 02_query_plan -> 03_search -> 04_filter -> 05_scrape -> 06_persist -> 07_extract -> 08_assess -> 09_score`.
- Persist each stage start/complete event and payload hash for replay/debug.

### Why
- Current thread model drops work on process restart and does not guarantee at-least-once completion.
- Stage graph simplifies partial reruns and failure isolation.
- Better support for retries and backpressure in external API calls.

### Tradeoffs
- Higher infra complexity (broker/workflow service, workers, queues).
- Increased operational surface (monitoring queues, dead-letter queues, worker pools).
- Latency can be slightly higher due to queue handoffs, but overall reliability improves.

### Scale effect
- Queue-based execution decouples API traffic from expensive research work, protecting ingress during spikes.
- Enables horizontal scaling of workers for heavy pipeline tasks (search/scrape/LLM).
- Stage-level parallelism (e.g., per-pillar parallel execution) reduces end-to-end latency as stock volume grows.

### Production best practices
- Keep stage handlers idempotent and side-effect safe.
- Configure per-task timeout, retry policy, and exponential backoff with retry budgets.
- Add DLQ for permanent failures and operational requeue controls.
- Emit per-stage status transitions (`queued`, `running`, `retrying`, `failed`, `completed`) in DB/events.

---

## 3) Domain/Application Services Layer (Pipeline + Scoring)

### Decision
- Refactor into explicit application services:
  - `QueryPlanService`
  - `SearchOrchestratorService`
  - `FilteringService`
  - `ScrapeService`
  - `EvidenceService`
  - `ScoringService`
- Keep LLM-based evidence extraction as an optional mode with deterministic fallback always available.
- Formalize contracts between services with dataclasses/Pydantic models.
- Make scoring logic pure and deterministic for a given input payload.

### Why
- Existing flow is already stage-oriented; service extraction preserves architecture while improving testability.
- Deterministic fallback prevents full pipeline failure when LLM providers or prompts degrade.
- A clean service contract lowers regression risk in CI gates.

### Tradeoffs
- More module boundaries and abstractions add refactor churn up front.
- Deterministic fallback can mask upstream data-quality issues if not clearly surfaced.
- Need clear policy on when “deterministic only” is acceptable versus requiring LLM confidence.

### Scale effect
- Isolated services enable targeted horizontal scaling (e.g., only scoring workers for bursts in post-processing).
- Deterministic scoring allows aggressive caching and faster recomputation, which benefits high-volume repeat queries.
- Service boundaries make load-testing each stage straightforward and more accurate.

### Production best practices
- Keep orchestration thin and stateless.
- Keep rule constants (weights, thresholds, thresholds by pillar) in versioned configuration.
- Add contract tests for every service boundary with fixture inputs.
- Track explainability outputs (coverage, confidence, evidence count) in every stage result.

---

## 4) Provider & Integrations Layer (Search, LLM, Scraping)

### Decision
- Introduce provider adapters behind interfaces:
  - `SearchProvider` (Tavily/SerpAPI/DDG)
  - `LLMProvider` (OpenAI/backup)
  - `ScrapeProvider` (requests-first + browser fallback)
- Add provider health checks, circuit breakers, and adaptive fallback chain.
- Normalize all provider outputs to canonical result schema immediately after fetch.

### Why
- The system already supports multiple providers but has provider branching in function-level logic.
- Adapter interfaces reduce lock-in and simplify switching/adding providers.
- Canonical schema protects downstream stages from inconsistent response shapes.

### Tradeoffs
- Adapter indirection adds code but improves control and observability.
- More failure paths to handle (provider degraded yet still returning partial data).
- Need continuous contract tests against third-party behavior changes.

### Scale effect
- Adaptive provider selection lowers peak dependency risk and protects throughput when one provider is throttled.
- Normalization + caching reduces repeated parsing cost across repeated research jobs.
- Per-provider concurrency caps prevent global saturation from one slow/degraded dependency.

### Production best practices
- Timeouts at connection/read/write levels; total budget and retries with jitter.
- Request idempotency keying where supported.
- Rate-limit budgets per provider + tenant and per-run cost guardrails.
- Centralized prompt versioning and strict prompt schema for parsing.
- HTML parsing hardening (size limits, domain allowlist, malformed-page defense).

---

## 5) Data Layer (Run metadata, artifacts, and events)

### Decision
- Replace SQLite (`artifacts/research_runs.db`) with PostgreSQL for run metadata/events.
- Keep artifact blobs in object storage (S3-compatible), with DB-stored pointers and metadata.
- Add indexes for `status`, `tenant`, `created_at`, and composite index for queue polling patterns.
- Persist stage events in an immutable event table/log and optionally emit to stream bus.

### Why
- Single-file SQLite is a bottleneck for concurrent writers and retention-heavy usage.
- Object storage is cheaper and more durable for files and scale-friendly.
- Immutable event logs simplify audits and post-mortems.

### Tradeoffs
- Migration complexity from file-based local storage.
- Slightly higher latency for artifact fetch unless signed-URL/caching is tuned.
- More operational components (DB maintenance, object-store lifecycle policies).

### Scale effect
- Supports high concurrency and higher write rates from many workers.
- Easier historical retention/search (analytics and model-quality dashboards).
- Better failure recovery because metadata is transactional and durable.

### Production best practices
- Use migrations (Alembic) for schema versioning.
- Add retention rules for events/artifacts (hot/cold tiers).
- Encrypt sensitive fields at rest and in transit.
- Add soft-delete or archival process for large historical runs.

---

## 6) API Persistence & Read Model

### Decision
- Keep separate read model tables/materialized views for list/detail endpoints (`/research-runs`, `/results`).
- Store summary/result JSONB documents for fast retrieval and partial indexing by top-level fields.
- Add explicit event ordering and monotonic progress semantics.

### Why
- List/detail endpoints currently do full object parsing from raw JSON strings; read models speed up common paths.
- JSONB enables flexible payload evolution while retaining queryability.
- Progress semantics reduce inconsistent UI states.

### Tradeoffs
- Denormalization can drift from source artifacts if write path isn’t enforced.
- Need schema evolution discipline for summary/result structure.

### Scale effect
- O(1)-like user-facing reads with indexed summaries even as run count grows.
- Faster dashboards and polling clients due to lighter query shape.

### Production best practices
- Use versioned projection logic to avoid old clients breaking.
- Strictly define progression semantics (`running` vs `retrying`, etc.).
- Add eventual consistency notes to docs if result projection lags by few seconds.

---

## 7) Observability Layer

### Decision
- Add OpenTelemetry tracing across API, queue, and each pipeline stage.
- Emit structured logs with mandatory context (`run_id`, `ticker`, `stage`, `provider`, `pillar`, `latency_ms`, `retry_count`).
- Centralize metrics: latency histograms, success/error by stage, provider error rates, queue depth, scrape hit-rate.

### Why
- This workflow has many external dependencies and non-deterministic LLM behavior; operational visibility is essential.
- Debugging stage-by-stage is otherwise expensive and slow.

### Tradeoffs
- Observability overhead in bytes and processing, especially for high-cardinality labels.
- Requires instrumentation discipline and retention cost.

### Scale effect
- At scale, early anomaly detection (e.g., one provider becoming noisy) avoids cascading failures.
- Better capacity planning and cost control when job volume increases.

### Production best practices
- Add dashboards + alerts for p95 stage latency and retry spikes.
- Define SLOs (e.g., search p95 < X ms, pipeline success rate > Y%).
- Include cost telemetry per run (LLM tokens, external API calls).

---

## 8) Security, Compliance, and Access Layer

### Decision
- Secrets and keys from secret manager (never in repo/env for local-only debug).
- Tenant-aware auth + authorization on run creation and run lookup.
- Outbound egress control and request signing/audit logs.
- Add explicit data-retention + GDPR/data handling policy for stored web content and artifacts.

### Why
- The system handles external data and sensitive market intelligence workflows; least privilege and auditability are mandatory for production trust.
- Content provenance matters for legal/compliance confidence.

### Tradeoffs
- Strong access controls increase integration friction with internal tools.
- Secret manager and audit pipeline increases infra setup overhead.

### Scale effect
- Controlled blast radius under token/key compromise.
- Easier enterprise adoption with role-based restrictions and audit trail requirements.

### Production best practices
- Rotate API keys periodically; scope keys per environment.
- Add redaction in logs (avoid keys, internal URLs with credentials).
- Significant actions should require idempotency key + audit entries.

---

## 9) Testing, Quality Gates, and Release Layer

### Decision
- Extend existing contract tests into a stage-specific suite: query-schema, search normalization, filtering behavior, scoring outputs, and orchestration idempotency.
- Add performance/load tests for: concurrent runs, burst submission, provider throttling.
- Add chaos tests for provider failures and timeout injection.

### Why
- Current repo already uses contract gates (good baseline). Scaling requires stress and failure-path confidence.
- Release risk is highest at stage boundaries and provider adapters.

### Tradeoffs
- Longer CI pipelines and higher compute costs.
- Requires reliable test fixtures for realistic external data shape.

### Scale effect
- Early regression detection improves confidence for multi-tenant production traffic.
- Better release velocity because failures are caught pre-production at behavior boundaries.

### Production best practices
- Keep contracts deterministic (mock all external systems in CI).
- Add golden snapshots for scoring outputs per fixture + threshold-based deltas.
- Add policy checks in CI: lint + type-check + security scan + container scan + tests.

---

## 10) Deployment and Runtime Layer

### Decision
- Split deployment into API tier + worker tier in containers.
- Use managed DB/Redis/object storage in production.
- Blue/green or rolling deploy for API and workers, with health-driven promotion.

### Why
- Decoupled tiers prevent heavy jobs from impacting API latency.
- Managed infrastructure reduces operational burden and improves durability.

### Tradeoffs
- Additional CI/CD complexity and infra cost (multi-service setup).
- Worker-version mismatches with API need strict compatibility checks.

### Scale effect
- Linear scaling by adding worker replicas during long-running research spikes.
- API stays responsive even under research backlog.

### Production best practices
- Environment parity (`local -> staging -> prod`) with same queue and migration process.
- Health/readiness probes for liveness and dependency checks.
- Deployment playbooks for schema migrations before worker rollout.

---

## Design decision summary (compact)

- Durable queue/workflow over threads: better reliability and restart safety, with higher infra complexity.
- PostgreSQL + object storage over file-based local DB/artifacts: better concurrency and governance, with migration and ops overhead.
- Adapter-based providers over inline provider branching: greater resilience and vendor portability, with more abstraction code.
- Async API + explicit orchestration state: better throughput and observability, with distributed system complexity.
- Deterministic fallback path preserved: stable baseline behavior under LLM/provider instability, with caution on confidence governance.

---

## What this unlocks at scale

- Higher job throughput with bounded concurrency and retryable workers.
- Predictable failure domains instead of silent data-path collapse.
- Better cost controls (LLM calls gated, caching, provider fallback policies).
- Clear auditability and production observability for investment-research outputs.

