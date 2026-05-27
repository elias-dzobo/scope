# Harness Implementation Strategy

Phased plan to address every issue raised in `docs/harness-evaluation.md` and
`docs/system-design-evaluation.md`. Each phase is independently deployable and
testable. No phase requires the next one to be complete.

---

## Phase 0 — Critical Fixes (Before Any Paid Beta)
**Time estimate: 2–3 days**
**Goal: Eliminate the failure modes that directly harm users.**

---

### 0.1 Persist intermediate results before synthesis

**File:** `apps/api/src/scope_api/application/run_service.py`

After pillar scoring completes and before the synthesis LLM call, write the
partial result to `result_json` and set `status = "synthesizing"`. If synthesis
fails, set `status = "completed_partial"` and emit a `synthesis_failed` event.

```python
# In _run_pipeline_job, after scoring:
intermediate = {
    "ticker": ticker,
    "scorecard": scorecard,
    "pillarAssessments": pillar_assessments,
    "evidenceByPillar": evidence_by_pillar,
    "sourcesByPillar": sources_by_pillar,
    "runtimeProfile": runtime_stats,
}
db.update_run(run_id, {"result_json": intermediate, "status": "synthesizing"})

try:
    synthesis = controller.synthesize(...)
    final = {**intermediate, "finalSynthesis": synthesis}
    db.update_run(run_id, {"result_json": final, "status": "completed"})
except Exception as exc:
    logger.error("synthesis_failed run_id=%s error=%s", run_id, exc)
    db.append_run_event(run_id, "synthesis_failed", {"error": str(exc)})
    db.update_run(run_id, {"status": "completed_partial"})
```

**Acceptance:** Kill the synthesis process mid-call. The run shows `completed_partial`
and the frontend renders the scorecard and evidence without the memo.

---

### 0.2 Emit observable events for all silent fallbacks

**File:** `packages/research-core/src/research_core/harness/` — `grounding.py`, `gates.py`, `runner.py`

Add one `emit_observation` call in every degradation path:

| Location | When | Event |
|----------|------|-------|
| `grounding.py:call_gemini_grounded` | `GOOGLE_API_KEY` missing | `grounding_unavailable` |
| `grounding.py:_extract_evidence_llm` | OpenAI call fails | `extraction_degraded` |
| `gates.py:_apply_llm_alignment_judgement` | OpenAI call fails | `judge_unavailable` |
| `runner.py:_run_targeted_fallbacks` | Gate failed, using fallback | `gate_fallback_triggered` |
| `run_service.py` | Gate still fails after fallback | `gate_exhausted` |

Each event is appended to `run_events` and written to the JSONL trace. No event is
dropped silently.

**Acceptance:** Remove `GOOGLE_API_KEY`, start a research run, check `run_events` —
`grounding_unavailable` event should appear.

---

## Phase 1 — Plan Executability
**Time estimate: 3–4 days**
**Goal: Make the planner's output drive tool selection at runtime.**

---

### 1.1 Add `tool_assignment` and `query_hints` to `ResearchWorkstream`

**File:** `packages/research-core/src/research_core/harness/models.py`

```python
class ResearchWorkstream(BaseModel):
    ...
    tool_assignment: Literal[
        "gemini_grounded",
        "search_and_parse",
        "legacy_pipeline",
    ] = "gemini_grounded"
    query_hints: list[str] = Field(default_factory=list)
```

---

### 1.2 Add `ToolRegistry` to the runner

**File:** `packages/research-core/src/research_core/harness/runner.py`

Replace the hardcoded `tools.run_grounded_workstream_research(brief, workstream)` call
with a dispatch:

```python
def _execute_workstream(self, brief, workstream, progress_callback):
    tool = self._tool_registry[workstream.tool_assignment]
    return tool.run(brief, workstream, progress_callback)
```

```python
self._tool_registry = {
    "gemini_grounded": GroundingTool(self.tools),
    "search_and_parse": SearchAndParseTool(self.tools),
    "legacy_pipeline": LegacyPipelineTool(self.tools),
}
```

Each tool is a simple callable that wraps the existing `ResearchToolFacade` methods.
No new LLM calls — just dispatch.

**Acceptance:** Set a workstream's `tool_assignment = "legacy_pipeline"`.
That workstream runs through the legacy pipeline while others run grounding.
The gate evaluates all results together.

---

### 1.3 Update the planner to set `tool_assignment`

**File:** `packages/research-core/src/research_core/harness/planner.py`

In `_create_company_plan_deterministic`, set tool assignment per pillar based on
key availability:

```python
def _tool_for_pillar(self, pillar: str) -> str:
    if os.getenv("GOOGLE_API_KEY", "").strip():
        return "gemini_grounded"
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "search_and_parse"
    return "legacy_pipeline"
```

---

## Phase 2 — Gate → Re-plan Loop
**Time estimate: 3–4 days**
**Goal: Replace the fixed fallback with a targeted re-planning step.**

---

### 2.1 Add `replan_from_gate_gaps`

**File:** `packages/research-core/src/research_core/harness/runner.py`

Replace `_run_targeted_fallbacks` with a re-plan function that reads gap reasons
from `QualityGateResult` and generates `query_hints` for each weak workstream.

```python
def _replan_from_gate_result(
    self,
    brief: ResearchBrief,
    plan: ResearchPlan,
    gate_result: QualityGateResult,
) -> None:
    """Update weak workstreams with targeted query hints from gate gaps."""
    for gap_description in gate_result.gaps:
        workstream = _find_workstream_for_gap(plan, gap_description)
        if workstream is None:
            continue
        hints = _derive_query_hints(brief, workstream, gap_description)
        workstream.query_hints = hints
        workstream.status = WorkstreamStatus.PENDING
        workstream.iteration_count += 1
```

```python
def _derive_query_hints(brief, workstream, gap: str) -> list[str]:
    name = brief.entities[0].name if brief.entities else ""
    ticker = brief.entities[0].ticker if brief.entities else ""
    hints = []
    if "no aligned facts" in gap or "thin_content" in gap:
        hints.append(f"{name} {workstream.pillar_name.lower()} analysis {ticker}")
    if "no primary source" in gap or "primary" in gap:
        hints.append(f"{name} annual report investor relations {ticker}")
    if "stale" in gap:
        hints.append(f"{name} latest results 2025 {ticker}")
    if "P/E" in gap or "multiple" in gap or "valuation" in gap.lower():
        hints.append(f"{ticker} trailing P/E forward multiple analyst consensus")
    if not hints:
        hints.append(f"{name} {ticker} {workstream.search_focus[0] if workstream.search_focus else ''}")
    return hints[:3]
```

---

### 2.2 Honour `query_hints` in grounding and search tools

**File:** `packages/research-core/src/research_core/harness/grounding.py`

In `build_grounded_prompt`, inject `query_hints` into the prompt when present:

```python
def build_grounded_prompt(brief, workstream):
    base = ...  # existing prompt
    if workstream.query_hints:
        hints_text = "\n".join(f"- {h}" for h in workstream.query_hints)
        base += f"\n\nPriority search angles:\n{hints_text}"
    return base
```

---

### 2.3 Enforce `max_iterations` in the runner

**File:** `packages/research-core/src/research_core/harness/runner.py`

```python
for iteration in range(plan.stop_conditions.max_plan_iterations):
    self._execute_pending_workstreams(brief, plan, progress_callback)
    gate_result = self.gates.evaluate_company_research(plan, current_result)
    plan.add_gate_result(gate_result)

    if gate_result.passed:
        break
    if iteration < plan.stop_conditions.max_plan_iterations - 1:
        self._replan_from_gate_result(brief, plan, gate_result)
        self._emit_event("gate_retry", {"iteration": iteration + 1, "gaps": gate_result.gaps})
    else:
        self._emit_event("gate_exhausted", {"iterations": iteration + 1})
```

**Acceptance:** Run a company with no Gemini key. Gate fails. Re-plan triggers.
Second iteration runs with `query_hints`. `gate_retry` event appears in `run_events`.

---

## Phase 3 — Alignment Scoring Quality
**Time estimate: 2 days**
**Goal: Reduce false rejections on qualitative pillars.**

---

### 3.1 Per-pillar alignment thresholds

**File:** `packages/research-core/src/research_core/harness/gates.py`

```python
PILLAR_ALIGNMENT_THRESHOLDS: dict[str, float] = {
    "Macro & Industry": 0.50,
    "Economic Moat": 0.50,
    "Financial Engine": 0.65,
    "Management & Capital Allocation": 0.60,
    "Valuation": 0.65,
    "Technical Analysis": 0.55,
}
```

Replace:
```python
if score >= self.minimum_alignment_score:
```
With:
```python
threshold = PILLAR_ALIGNMENT_THRESHOLDS.get(pillar, self.minimum_alignment_score)
if score >= threshold:
```

**Acceptance:** Run Macro & Industry gate with 3 qualitative facts (no metric_value).
Gate should pass. Previously it failed due to `thin_content` reasons on qualitative excerpts.

---

### 3.2 Batch LLM alignment judge

**File:** `packages/research-core/src/research_core/harness/gates.py`

Replace per-fact LLM calls with a single batched call per workstream.

```python
def _batch_judge_workstream(
    self,
    pillar: str,
    facts: list[dict],
    workstream: ResearchWorkstream,
) -> list[dict]:
    """Score all facts for a workstream in one LLM call."""
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return [self._score_evidence_alignment(pillar, f, workstream) for f in facts]
    try:
        results = self._llm_batch_judge(pillar, facts, workstream)
        return results
    except Exception:
        self._emit_event("judge_unavailable", {"pillar": pillar})
        return [self._score_evidence_alignment(pillar, f, workstream) for f in facts]
```

**Acceptance:** Verify LLM calls per gate evaluation drops from N (number of facts)
to 1 per pillar (6 total per run instead of up to 180).

---

### 3.3 Cross-pillar evidence deduplication

**File:** `packages/research-core/src/research_core/harness/runner.py`

In `_merge_grounded_and_document_evidence`, deduplicate facts across pillars
by (excerpt_hash, source_url) before evidence is passed to the gate.

```python
def _dedup_evidence(evidence_by_pillar: dict) -> dict:
    seen: set[str] = set()
    deduped: dict = {}
    for pillar, facts in evidence_by_pillar.items():
        deduped[pillar] = []
        for fact in facts:
            key = f"{hash(fact.get('excerpt', ''))}-{fact.get('source_url', '')}"
            if key not in seen:
                seen.add(key)
                deduped[pillar].append(fact)
    return deduped
```

---

## Phase 4 — Infrastructure Simplification
**Time estimate: 2 days**
**Goal: One execution path. No legacy branching.**

---

### 4.1 Delete in-process orchestrator

**File:** `apps/api/src/scope_api/orchestration.py`

Delete the file. Remove all imports and references in `app.py`, `service.py`, `worker.py`.
`RESEARCH_EXECUTION_BACKEND` env var is removed from documentation and `fly.toml`.

All environments use durable mode. Local dev uses SQLite + worker.

**Migration:** Confirm the worker polls correctly on local SQLite by running:
```bash
uv run python -m scope_api.worker &
uv run python api_main.py
```

---

### 4.2 SSE streaming endpoint

**File:** `apps/api/src/scope_api/app.py`

Add a non-breaking SSE endpoint alongside the existing polling endpoint:

```python
@router.get("/research-runs/{run_id}/events")
async def stream_run_events(run_id: str, user=Depends(optional_current_user)):
    async def generator():
        last_id = 0
        while True:
            rows = db.get_run_events_after(run_id, last_id)
            for row in rows:
                yield f"event: {row['stage']}\ndata: {json.dumps(row['payload_json'])}\n\n"
                last_id = row["id"]
            status = db.get_run_status(run_id)
            if status in ("completed", "completed_partial", "failed"):
                yield "event: terminal\ndata: {}\n\n"
                break
            await asyncio.sleep(1)
    return StreamingResponse(generator(), media_type="text/event-stream")
```

Frontend migrates to `EventSource("/api/v1/research-runs/{id}/events")` and drops
the 3-second poll loop.

---

## Phase 5 — Compatibility Shim Removal (After Stable Deploy)
**Time estimate: 1 day**
**Pre-condition: All tests pass with canonical imports. Production deploy is stable.**

- Delete `src/` directory (legacy shims)
- Delete root-level `research_core/`, `scope_api/`, `provider_integrations/` shim packages
- Update all remaining scripts and tests to canonical import paths
- Verify CI is green

---

## Implementation Order

```
Phase 0.1 — Intermediate result persistence    ← ship immediately
Phase 0.2 — Observable fallbacks               ← ship immediately
Phase 1   — Plan executability                 ← sprint 1
Phase 2   — Gate → re-plan loop                ← sprint 1
Phase 3   — Alignment scoring quality          ← sprint 2
Phase 4.1 — Delete in-process orchestrator     ← sprint 2
Phase 4.2 — SSE streaming                      ← sprint 2
Phase 5   — Shim removal                       ← after stable deploy
```

---

## Acceptance Criteria (Overall)

| Criterion | How to verify |
|-----------|--------------|
| Synthesis failure leaves recoverable result | Kill synthesis mid-call; run shows `completed_partial`; scorecard renders |
| All fallbacks are observable | Remove `GOOGLE_API_KEY`; `grounding_unavailable` in `run_events` |
| Re-plan improves weak pillars | Mock a gate failure; verify second iteration uses `query_hints` in Gemini prompt |
| Qualitative pillars pass gate | Run Macro & Industry with 3 text-only facts; gate passes with 0.50 threshold |
| LLM judge is batched | One OpenAI call per pillar in gate stage, not one per fact |
| No duplicate evidence | Same fact from two sources; appears once in each pillar's evidence list |
| In-process mode removed | `RESEARCH_EXECUTION_BACKEND` not required; worker handles all envs |
| SSE replaces polling | Frontend uses `EventSource`; 0 polling requests during research run |
