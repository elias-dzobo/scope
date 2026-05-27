# Advisor Feature — Honest Evaluation

**Status:** Pre-production  
**Date:** 2026-05-22  
**Scope:** `apps/api/src/scope_api/advisor.py`, `apps/api/src/scope_api/memory.py`

---

## Executive Summary

The advisor feature is a promising product vision built on fragile foundations. The 6-step harness loop is architecturally sound in concept, but nearly every layer contains hard-coded heuristics, synchronous blocking calls, and untestable decision branches that will fail silently in production at scale. Memory retrieval is purely lexical — a missing vector index means semantic recall quality degrades as the graph grows. The answer quality is entirely dependent on LLM availability; the deterministic fallback produces useful answers for generic-research questions but generates repetitive, hardcoded prose (`"Yes. The way I'd break this down is by asking..."`) regardless of the actual question.

---

## 1. Requirements Gaps

### 1.1 Conversation State is Ephemeral
**Severity: High**

Conversation state (active entities, active themes, active research IDs) is stored in the `advisor_conversations` table but is only updated at the end of a successful turn. A partial run, an exception, or a sync failure leaves the state stale. There is no locking or optimistic concurrency control — two concurrent advisor turns on the same conversation will silently corrupt state.

### 1.2 No Defined SLA or Timeout Budget
**Severity: High**

`_run_targeted_research()` calls `controller.run_company_research()` or `GenericFinancialResearchHarness.run()` **synchronously inside the request/response cycle**. Full company research takes 60–180 seconds. This will exhaust API gateway timeouts, block the event loop in a sync FastAPI app (uvicorn default mode), and provide no feedback to the user while they wait.

### 1.3 No Answer Quality Contract
**Severity: High**

There is no defined quality gate for the advisor answer. `_answer_looks_incomplete()` checks for trailing punctuation and whether the answer is too short when fresh research was returned, but there is no semantic check. The LLM can return `"I don't know"` with perfect punctuation and the system will serve it with `confidence=high`. There is no automated grading of advisor answer quality before it reaches the user.

### 1.4 `allow_deep_research=False` by Default
**Severity: Medium**

The `allowDeepResearch` field defaults to `False` in `AdvisorRunRequest`. In practice the advisor planner will decide `should_run_fresh_research=True` for most substantive queries, but `_run_targeted_research()` is only called when the frontend opts in. The current product behavior is therefore: identify a research gap, communicate it to the user, but never fill it automatically. This is acceptable as a phased rollout but is a UX problem — the advisor says "you need fresh research" but never runs it by default.

### 1.5 `MAX_INLINE_RESEARCH_REQUESTS = 1`
**Severity: Medium**

When the advisor triggers company research, only one ticker is processed even if the planner identifies multiple. A question like "compare NVDA and AMD" will only research the first resolved ticker. The second is silently dropped.

### 1.6 Memory Write-back on Exception is Silent
**Severity: Medium**

`_write_advisor_memory()`, `_write_targeted_research_memory()`, and `_write_generic_research_memory()` are called at the end of `run_advisor_harness()` without any error handling. A DB write failure silently leaves the graph corrupted — the turn happened but no memory trace was written. The user gets a normal response, but the next turn will see a gap.

---

## 2. Memory Retrieval Architecture Weaknesses

### 2.1 Purely Lexical Retrieval
**Severity: High**

`plan_user_context()` in `memory.py` performs lexical search (`ILIKE` or full-text search on chunk text). As the user's memory graph grows, lexical recall degrades on semantic queries. A user asking "what did I learn about AI infrastructure exposure?" will not retrieve chunks about "semiconductor supply chains" or "hyperscaler capex" if those exact words are absent from the query. This is the single biggest quality risk at scale.

**Impact:** Query-specific and terminology-sensitive recall means memory appears thin even when relevant research exists.

### 2.2 Context Scoring Weights are Unvalidated Constants
**Severity: Medium**

The context scoring formula in `memory.py` uses fixed weights:
- Lexical match: 35%
- Entity match: 30%
- Freshness: 20%
- Graph proximity: 10%
- Profile: 5%

These weights were chosen heuristically and have never been validated against real user queries. A user asking about a company they haven't mentioned in 6 months will be penalized by freshness even though their saved research is the best available answer.

### 2.3 Graph Proximity is Computed Naively
**Severity: Medium**

Graph proximity (10% of context score) is calculated by BFS hop count in the in-memory Python graph. This does not scale past a few hundred nodes per user without a proper graph DB or at minimum a persisted adjacency list. The current approach re-derives the graph on every retrieval request.

### 2.4 No Cache or Pre-computation
**Severity: Low-Medium**

The entire retrieval plan — LLM planner call, chunk search, node scoring, coverage assessment — runs synchronously on every advisor turn with zero caching. For users with large memory graphs this is unnecessarily expensive and slow.

---

## 3. Planning Layer Weaknesses

### 3.1 LLM Planner Runs on Every Turn
**Severity: Medium**

`_build_advisor_research_plan()` calls `gpt-4o-mini` on every advisor turn, even for simple follow-up questions that could be resolved purely from thread state. The deterministic fallback is available but only used when `OPENAI_API_KEY` is absent.

### 3.2 `_is_research_like_query()` is a Keyword List
**Severity: Medium**

The function that decides whether to trigger fresh research is a 20-word keyword list (`"latest"`, `"current"`, `"government"`, `"contract"`, etc.). It will misfire in both directions:
- False positive: `"what is the current state of Apple's moat?"` triggers fresh research when saved memory is sufficient.
- False negative: `"Is NVDA well-positioned for data center growth?"` does not match any keyword and will not trigger research even though it needs current data.

### 3.3 Mode Resolution Has Competing Signal Sources
**Severity: Low**

`_resolve_mode()`, `_build_advisor_research_plan_deterministic()`, `_is_generic_or_comparative_question()`, and `_is_research_like_query()` each independently classify the query intent. They can produce conflicting signals (one says `company_research`, another forces `generic_research`). There is no single canonical intent classification.

---

## 4. Synthesis Layer Weaknesses

### 4.1 Deterministic Fallback Has Hardcoded Prose
**Severity: High**

`_generic_memory_fallback_answer()` opens with the hardcoded string:
```
"Yes. The way I'd break this down is by asking: which companies are closest to unavoidable AI infrastructure spend?"
```
This string appears **regardless of the question asked**. A user asking about drug approvals or European retail stocks will receive this AI-infrastructure framing. This is a product quality bug.

### 4.2 No Hallucination Guard
**Severity: High**

`AdvisorSynthesizer.generate()` passes up to 8 research chunks, 8 evidence refs, and 3 fresh research results to `gpt-4o-mini`. There is no post-generation factual verification step. The LLM can synthesize claims that contradict the evidence it was given. The system prompt instructs the model not to invent facts, but there is no automated check.

### 4.3 `_answer_looks_incomplete()` Logic is Fragile
**Severity: Medium**

The incomplete-answer detector checks for trailing punctuation, odd bold-marker count, and answer length relative to fresh research synthesis length. It will:
- Fail to detect answers that end with a complete sentence but contain hallucinated data.
- False-positive on answers that intentionally contain bold headers (odd `**` count).
- Miss truncated list items that end with a period.

### 4.4 Stance is Set Before Synthesis
**Severity: Low**

`stance` is determined before the LLM synthesizes the answer (`"answered_with_fresh_research"`, `"answered_from_memory"`, etc.). If the LLM acknowledges uncertainty in its answer text, the stance is still `"answered_with_fresh_research"`, which is misleading metadata.

---

## 5. Observability and Testability

### 5.1 No Automated Eval Coverage
**Severity: High**

`tests/test_advisor_evals.py` contains only 2 placeholder tests that delegate to a `deepeval` runner that has no test cases. There is **zero automated coverage** of:
- Plan mode selection correctness
- Memory retrieval quality
- Answer quality / hallucination rate
- Fallback answer coherence
- Memory write-back correctness
- Conversation state consistency

### 5.2 Trace Events are Incomplete
**Severity: Medium**

The advisor trace (stored in the DB) captures step names and statuses but does not include:
- Latency per step
- Token counts per LLM call
- Which memory chunks were retrieved and their scores
- Whether the final answer used retrieved context or generated novel content

---

## 6. Priority Ranking

| # | Issue | Severity | Effort |
|---|-------|----------|--------|
| 1 | Synchronous research blocks API thread | High | Medium |
| 2 | Hardcoded prose in fallback answer | High | Low |
| 3 | No vector retrieval — purely lexical | High | High |
| 4 | No answer quality gate / hallucination check | High | Medium |
| 5 | No automated eval coverage | High | Medium |
| 6 | allow_deep_research=False by default (UX gap) | Medium | Low |
| 7 | LLM planner on every turn | Medium | Low |
| 8 | `_is_research_like_query()` keyword list | Medium | Low |
| 9 | Memory write-back silent on failure | Medium | Low |
| 10 | Context scoring weights unvalidated | Medium | Medium |
| 11 | Stance set before synthesis | Low | Low |

---

## 7. What is Working Well

- The 6-step loop structure is sound and follows a coherent design contract.
- `_validate_advisor_research_plan()` properly constrains the LLM planner output.
- `_research_chunks()` correctly excludes prior advisor answers from the evidence lane, preventing recursive answer loops.
- Memory write-back is decoupled from the answer path — a write failure does not fail the user request.
- The `AdvisorAnswer` schema is well-designed with stance, confidence, and evidence refs.
- The `_thread_context()` function correctly limits conversation history to avoid bloating LLM prompts.
