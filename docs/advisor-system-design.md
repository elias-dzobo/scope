# Advisor Feature — System Design

**Status:** Recommended Architecture  
**Date:** 2026-05-22  
**Supersedes:** Current `advisor.py` + `memory.py` implementation

---

## 1. Design Goals

1. **Async-first.** Research should never block the HTTP response. The advisor response returns immediately; research runs in the background and the result is streamed or polled.
2. **Semantic memory.** Retrieval must be semantic (vector) not just lexical. Lexical search stays as a cheap reranking signal, not the primary retrieval mechanism.
3. **Answer quality contract.** Every advisor answer must pass a lightweight automated quality check before being returned. The check covers completeness, factual grounding, and absence of hardcoded boilerplate.
4. **Observable.** Every step emits structured trace events with latency, token counts, and retrieval scores.
5. **Testable.** Every decision branch (plan mode, stance, fallback trigger) must be exercisable from unit tests without live LLM or DB calls.

---

## 2. High-Level Architecture

```
User request
     │
     ▼
AdvisorOrchestrator (new)
     ├─ Step 1: ContextPlanner         (deterministic-first, LLM upgrade)
     ├─ Step 2: MemoryRetriever        (semantic + lexical hybrid)
     ├─ Step 3: CoverageGate           (structured decision)
     ├─ Step 4: ResearchPlanDecider    (deterministic-first, LLM upgrade)
     ├─ Step 5: BackgroundResearchLauncher  (async task queue)
     ├─ Step 6: AnswerSynthesizer      (LLM with quality gate)
     └─ Step 7: MemoryWriteBack        (async, fire-and-forget with retry)

Research results arrive via:
     ResearchResultStore ──▶ AnswerSynthesizer (on poll/push)
```

---

## 3. Memory Retrieval Redesign

### 3.1 Hybrid Retrieval Pipeline

Replace the pure-lexical retriever with a two-stage pipeline:

**Stage 1 — Candidate generation (fast, cheap)**
- Vector search against embedded chunk text (pgvector or similar).
- Lexical fallback for short, specific queries (ticker names, metric names).
- Target: retrieve top-30 candidates in < 100ms.

**Stage 2 — Reranking (accurate, per-query)**
- Score each candidate on the 5-factor formula (lexical, entity, freshness, graph proximity, profile).
- Return top-8 for synthesis context.
- Target: full retrieval + reranking in < 200ms.

### 3.2 Validated Scoring Weights

The context scoring weights must be calibrated. Initial calibration approach:
1. Collect 100 advisor turns with human-rated ground-truth relevant chunks.
2. Run a grid search over weight combinations.
3. Pick weights that maximize NDCG@8 on the held-out set.
4. Re-run calibration quarterly as the memory graph structure changes.

### 3.3 Graph Pre-computation

The adjacency graph for graph-proximity scoring must be pre-computed, not re-derived per request. Options:
- Persist adjacency list in Redis with TTL-based invalidation when a new memory node is added.
- Use a proper graph DB (Neo4j, Memgraph) if the user base requires it at scale.

---

## 4. Planning Layer Redesign

### 4.1 Single Intent Classifier

Replace the four competing classification functions (`_resolve_mode`, `_is_research_like_query`, `_is_generic_or_comparative_question`, `_build_advisor_research_plan_deterministic`) with a single `IntentClassifier` class that produces a canonical `QueryIntent`:

```python
@dataclass
class QueryIntent:
    mode: Literal["thread_only", "memory_only", "hybrid_research",
                  "generic_research", "company_research", "clarify"]
    needs_fresh_research: bool
    entities: list[str]
    tickers: list[str]
    themes: list[str]
    reasoning: str
    classifier_source: Literal["deterministic", "llm"]
```

The deterministic classifier runs first. The LLM classifier upgrades it only for ambiguous cases (detected by confidence score from the deterministic path).

### 4.2 Research Trigger Redesign

Replace the keyword list in `_is_research_like_query()` with a 3-factor decision:

1. **Freshness signal** — does the query contain temporal markers? (`latest`, `Q4 2025`, `recent earnings`)
2. **Data-gap signal** — did the coverage gate find no matching chunks for the resolved entities?
3. **Entity-new signal** — does the query reference entities not previously seen in the user's memory graph?

Any one signal → `needs_fresh_research=True`. This is more precise than the keyword list.

---

## 5. Synthesis Layer Redesign

### 5.1 Remove Hardcoded Prose

`_generic_memory_fallback_answer()` must be replaced. The fallback answer should be generated from the actual retrieved context, not hardcoded AI-infrastructure framing. The new fallback:

```python
def _generic_memory_fallback_answer(*, query: str, result: dict) -> str:
    """Template-based fallback from actual research content, no hardcoded framing."""
    synthesis = result.get("synthesis", "").strip()
    findings = result.get("keyFindings", [])[:5]
    risks = result.get("risks", [])[:3]
    # Build from actual content, not fixed preamble
    ...
```

### 5.2 Answer Quality Gate

Add a `AnswerQualityChecker` that runs after synthesis before the response is returned:

```python
@dataclass
class AnswerQualityResult:
    passed: bool
    issues: list[str]
    confidence_adjustment: float  # e.g., -0.2 if thin evidence

class AnswerQualityChecker:
    def check(self, answer: AdvisorAnswer, context: dict, refs: list[dict]) -> AnswerQualityResult:
        issues = []
        # 1. Completeness: non-empty answer, non-empty key_points
        # 2. Grounding: at least one evidence ref matches a claim in the answer
        # 3. Boilerplate detection: check for known hardcoded strings
        # 4. Hallucination heuristic: does the answer contain specific metrics
        #    not present in any supplied chunk?
        ...
```

If the quality check fails, the system retries synthesis once with a more constrained prompt before serving the fallback.

### 5.3 Stance Derivation After Synthesis

Move stance derivation to after the LLM generates the answer. The synthesizer should return a `(answer, inferred_stance)` pair. `inferred_stance` is set by inspecting the answer text (does it contain uncertainty language? does it confirm it used research?), not the pre-synthesis decision path.

---

## 6. Async Research Architecture

### 6.1 Background Research Pattern

```
AdvisorTurn (sync, fast)
  ├─ Steps 1-4 (< 500ms): plan, retrieve, gate, decide
  ├─ Step 5: if needs_fresh_research:
  │     task_id = enqueue_research_task(ticker, pillars, user_id)
  │     return AdvisorAnswer(stance="research_launched", task_id=task_id)
  └─ if no fresh research needed:
        Step 6-7: synthesize + write → return answer

ResearchTask (async, background)
  ├─ Runs in background worker (Celery, ARQ, or background thread pool)
  ├─ On completion: writes to memory + sets task status = "ready"
  └─ Notifies via SSE event or sets a polling-friendly DB flag

Polling / SSE endpoint
  GET /advisor/research/{task_id}/result
  └─ Returns synthesized answer when research is ready
```

### 6.2 Immediate Answer with Research Pending

When research is launched asynchronously, the advisor should still return a useful immediate answer from memory:

```
→ Immediate answer: "Based on your saved research from 3 weeks ago, here is what I know.
   I have also launched fresh research for current data. I'll update you when it's ready."
```

This is strictly better than the current behavior where the user waits 60–180 seconds.

---

## 7. Memory Write-back Redesign

### 7.1 Retry-Safe Write-back

All memory writes must be:
- Idempotent (upsert, not insert).
- Wrapped in a try/except with structured error logging.
- Retried up to 3 times with exponential backoff on transient failures.
- Tracked in a `memory_write_audit` table so failed writes can be replayed.

### 7.2 Write-back Decoupling

Memory write-back should move fully to the background layer — it should never be in the critical path of the advisor response, even partially. Current code calls `_write_advisor_memory()` after synthesizing the answer but before returning it. Move this to a fire-and-forget `asyncio.create_task()` or background job.

---

## 8. Observability

### 8.1 Structured Trace Events

Each advisor step must emit a trace event with:
```python
{
    "step": "memory_retrieval",
    "status": "completed",
    "duration_ms": 142,
    "chunks_retrieved": 8,
    "top_chunk_score": 0.74,
    "retrieval_method": "hybrid",
    "payload": { ... }
}
```

### 8.2 Answer Quality Telemetry

Track per-turn:
- `synthesis_source`: `"llm"` | `"deterministic_fallback"` | `"no_answer"`
- `quality_check_passed`: bool
- `quality_issues`: list of issue codes
- `answer_length`: int
- `evidence_ref_count`: int

This data feeds the monthly advisor quality review.

---

## 9. Testing Architecture

The advisor must be testable at three levels:

**Unit (no LLM, no DB)**
- `IntentClassifier` — given query + thread state, assert `QueryIntent`
- `AnswerQualityChecker` — given `AdvisorAnswer` + context, assert pass/fail
- `_build_advisor_research_plan_deterministic()` — given coverage, assert plan mode

**Integration (mock LLM, real DB schema)**
- Full advisor harness turn with injected mock `AdvisorSynthesizer`
- Memory write-back correctness

**Eval (live LLM, fixed test cases)**
- 50 golden cases: query → expected stance, expected mode, quality thresholds
- Run nightly, not on every commit
