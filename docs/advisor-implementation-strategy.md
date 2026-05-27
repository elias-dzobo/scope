# Advisor Feature — Implementation Strategy

**Status:** Recommended  
**Date:** 2026-05-22

---

## Guiding Principles

1. Fix product-quality bugs before adding features.
2. Each phase must leave the system in a shippable, testable state.
3. No phase breaks the existing API contract (`AdvisorRunResponse` shape is stable).
4. The agentic rewrite is incremental — the existing harness stays live while new layers are introduced behind feature flags.

---

## Phase 0 — Critical Bug Fixes (1–2 days)

These are bugs that affect every production advisor turn today.

### 0.1 Remove Hardcoded Prose

**File:** `apps/api/src/scope_api/advisor.py`  
**What:** Replace the hardcoded `"Yes. The way I'd break this down is by asking: which companies are closest to unavoidable AI infrastructure spend?"` opening in `_generic_memory_fallback_answer()` with a template that pulls from actual research content.

**Acceptance criteria:**
- A question about European retail stocks returns a different opening sentence than a question about AI infrastructure.
- The test `test_fallback_answer_no_hardcoded_ai_prose` passes.

### 0.2 Add Memory Write-back Error Handling

**Files:** `apps/api/src/scope_api/advisor.py`  
**What:** Wrap `_write_advisor_memory()`, `_write_targeted_research_memory()`, `_write_generic_research_memory()` in try/except with structured logging. Failures must not propagate to the user response.

**Acceptance criteria:**
- A simulated DB failure in write-back does not cause a 500 on the advisor endpoint.
- A `memory_write_failed` event is logged with the error and user/turn context.

### 0.3 Expand Eval Coverage

**File:** `tests/test_advisor_evals.py`  
**What:** Replace the 2 placeholder tests with the 40+ unit tests covering all decision branches (see Phase 0 of the eval suite plan below).

---

## Phase 1 — Intent Classifier + Stance Decoupling (3–5 days)

### 1.1 Consolidate Intent Classification

Replace the four competing classifiers (`_resolve_mode`, `_is_research_like_query`, `_is_generic_or_comparative_question`, `_build_advisor_research_plan_deterministic`) with a single `AdvisorIntentClassifier` class.

**New file:** `apps/api/src/scope_api/advisor_intent.py`

```python
class AdvisorIntentClassifier:
    def classify(self, query: str, thread_context: dict, coverage: dict) -> QueryIntent:
        """Deterministic-first classification with structured confidence score."""
        ...
```

**Migration:** `_build_advisor_research_plan()` calls `AdvisorIntentClassifier.classify()` first, then optionally upgrades with LLM if deterministic confidence < 0.7.

### 1.2 Move Stance Derivation to Post-Synthesis

Remove stance pre-assignment from `_synthesize_answer()`. The synthesizer should set `stance` after inspecting the answer text. Add `infer_stance_from_answer(answer_text: str, context: dict) -> str` to `advisor_intent.py`.

### 1.3 Add Answer Quality Checker

**New class:** `AnswerQualityChecker` in `advisor_quality.py`

Checks:
1. Non-empty answer.
2. No known hardcoded boilerplate strings.
3. At least one key_point when evidence refs exist.
4. Answer length ≥ 120 chars when research was available.

If check fails → re-synthesize once with a stricter prompt, then serve fallback.

---

## Phase 2 — Async Research Decoupling (1 week)

### 2.1 Background Research Task

Introduce a `ResearchTaskQueue` abstraction:

```python
class ResearchTaskQueue:
    def enqueue(self, task: ResearchTask) -> str:  # returns task_id
        ...
    def get_result(self, task_id: str) -> ResearchTaskResult | None:
        ...
```

Initial implementation: in-process background thread pool (no new infrastructure).  
Later: ARQ or Celery worker.

### 2.2 Immediate Answer with Pending Research

When `should_run_fresh_research=True`:
1. Synthesize an immediate answer from memory (best available).
2. Enqueue research task.
3. Return `AdvisorAnswer(stance="research_launched", task_id=task_id, answer=<memory_answer>)`.

### 2.3 Result Retrieval Endpoint

```
GET /advisor/research/{task_id}/result
```

Returns the synthesized answer once the background research completes, or `{"status": "pending"}` while in progress.

### 2.4 Increase MAX_INLINE_RESEARCH_REQUESTS

Change from `1` to `3`. The background async pattern removes the latency penalty that forced it to 1.

---

## Phase 3 — Semantic Memory (2 weeks)

### 3.1 Vector Embedding on Memory Chunk Ingestion

When `create_memory_chunk()` is called, embed the chunk text using `text-embedding-3-small` (OpenAI) or a local model and store the embedding vector.

**Schema change:** Add `embedding vector(1536)` column to `memory_chunks` table. Add `ivfflat` or `hnsw` index via `pgvector`.

### 3.2 Hybrid Retrieval in `plan_user_context()`

Replace the pure-lexical `search_memory_chunks()` call with a two-stage pipeline:
1. ANN vector search → top-30 candidates.
2. Score + rerank by the 5-factor formula → top-8.
3. Fallback to pure lexical if embeddings are not yet available (migration window).

### 3.3 Graph Adjacency Pre-computation

Add a `memory_adjacency_cache` table or Redis key that stores the adjacency list per user, invalidated when a new node/edge is added. The `plan_user_context()` scorer reads from cache instead of re-deriving the graph.

### 3.4 Weight Calibration (First Pass)

- Collect 100 advisor turns with human-rated relevant chunks (internal team evaluation).
- Run grid search over scoring weights.
- Hardcode the best weights as named constants with a comment linking to the calibration doc.

---

## Phase 4 — Observability + Eval Harness (3–5 days)

### 4.1 Structured Advisor Trace

Each step writes a structured trace event (see System Design doc § 8.1). Trace is accessible via `GET /advisor/runs/{id}/trace`.

### 4.2 Answer Quality Telemetry

Track `synthesis_source`, `quality_check_passed`, `quality_issues`, `answer_length`, `evidence_ref_count` in the advisor run record and expose via an internal admin endpoint.

### 4.3 Nightly Eval Suite

50 golden test cases (query, expected mode, expected stance, quality thresholds) run nightly against a staging advisor instance. Results posted to an internal dashboard.

---

## Phase 5 — Production Hardening (ongoing)

- Conversation state optimistic locking (prevent concurrent write corruption).
- Memory write-back retry queue (persisted, not in-memory).
- LLM call circuit breaker (falls back to deterministic when error rate > 10%).
- Rate limiting per user on fresh research launches (max 3 per hour).
- Advisor answer human feedback collection endpoint.

---

## Rollout Plan

| Phase | Duration | Flag | Risk |
|-------|----------|------|------|
| 0 — Bug fixes | 1–2 days | Off (always on) | Low |
| 1 — Intent + quality | 3–5 days | `ADVISOR_QUALITY_CHECK=true` | Low |
| 2 — Async research | 1 week | `ADVISOR_ASYNC_RESEARCH=true` | Medium |
| 3 — Semantic memory | 2 weeks | `ADVISOR_VECTOR_RETRIEVAL=true` | Medium |
| 4 — Observability | 3–5 days | N/A | Low |
| 5 — Hardening | Ongoing | N/A | Low |
