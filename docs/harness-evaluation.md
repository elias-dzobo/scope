# Agentic Harness Evaluation

Honest assessment of the current research harness — what it does well, where it breaks,
and what needs to change before the harness can be described as truly agentic.

---

## What the Harness Does

The harness is a **staged pipeline with a single feedback loop and quality gates**. It is
not a fully agentic loop — it does not re-plan dynamically or make decisions about which
tools to call at runtime. It is a well-structured deterministic sequence of LLM-assisted
stages with one retry opportunity.

### Stages

```
Planning → Grounded Research → Document Discovery → Parsing →
Evidence Assembly → Quality Gates → [Fallback] → Scoring → Synthesis
```

Each stage has a defined input/output contract (`ResearchPlan`, `GroundedResearchResult`,
`ParsedDocument`, `EvidenceByPillar`, `QualityGateResult`, `FinalResearchSynthesis`).
Progress is emitted to the database after every stage so the frontend can track it.

### LLM Calls

| Stage | Model | What It Does |
|-------|-------|-------------|
| Planning | GPT-4o-mini | Generate workstreams with search focus + required evidence per pillar |
| Grounded Research | Gemini 2.5 Flash | Web search grounding — returns answers with citation supports |
| Document Extraction | GPT-4o-mini (optional) | Extract company-specific facts from parsed text |
| Alignment Judge | GPT-4o-mini (optional) | Override deterministic alignment score for each evidence fact |
| Final Synthesis | OpenAI (configurable) | Write investment memo with personalized recommendation |

### Quality Gates

Gates run after evidence assembly and check:
- Minimum aligned evidence facts per pillar (configurable threshold)
- Minimum source count per pillar
- Primary document required for Financial Engine, Management, Valuation pillars
- Financial-statement table required for Financial Engine
- Source freshness (configurable day threshold)
- Citation support present from Gemini (not just text answer)

If gates fail, the harness runs targeted fallback on the weakest pillars, then re-evaluates
gates once. If gates still fail, synthesis runs on whatever evidence exists.

---

## What Works Well

**Structured evidence contracts.** Every stage produces a typed Pydantic model.
The result shape is stable and the API contract is validated in CI.

**Graceful degradation.** Planning falls back to a deterministic plan if OpenAI is unavailable.
Grounding returns `status=unavailable` if the Gemini key is missing. Document extraction
falls back to keyword matching if LLM extraction fails. The pipeline always reaches synthesis.

**Deterministic scoring.** Pillar scoring and scorecard generation are fully deterministic —
no LLM randomness in the final numerical output. Scores are reproducible given the same evidence.

**Targeted fallback.** When gates fail on specific pillars, only those pillars get re-run
through the legacy pipeline. A full fallback is only triggered if all pillars fail.

**Memory indexing.** Completed runs are indexed into a GraphRAG graph (company nodes,
research run nodes, pillar assessment nodes, edges between them). This enables the advisor
to retrieve grounded context for follow-up questions.

**Observable execution.** Every stage emits progress to the database. The frontend can
show the current stage, substep, and activity count. The harness also appends a JSONL trace
file per run for debugging.

---

## Weaknesses & Failure Modes

### 1. The plan is never updated

**What happens:** The planner creates a `ResearchPlan` with 6 workstreams upfront. If grounding
returns poor results for Valuation, the plan is not revised. The workstream's `status` is
updated to `NEEDS_MORE_EVIDENCE`, but there is no mechanism to regenerate search queries,
refocus the workstream goal, or request different evidence types.

**Impact:** The harness cannot adapt mid-run. If Gemini finds weak Valuation sources (e.g.,
the company has no analyst coverage), the system cannot pivot to a different search strategy.
It falls back to the legacy pipeline, which runs fixed queries — also unrelated to the
original plan.

**What a truly agentic harness would do:** Observe that Valuation has no P/E multiple,
re-plan a targeted search for "trading comparables [ticker]", run it, and re-evaluate.

---

### 2. Grounding degradation is silent

**What happens:** If `GOOGLE_API_KEY` is not set, `call_gemini_grounded()` returns
`status="unavailable"` with no sources, no evidence, and no warning logged. The pipeline
continues as if grounding ran successfully.

**Impact:** A run with missing grounding key produces a research output based entirely on
document parsing and legacy pipeline fallback — a significantly weaker result. The user
sees no indication.

**Fix:** Log a `WARNING` at startup if `GOOGLE_API_KEY` is missing. Emit a `gate_gap` in
the first quality gate pass noting that grounding was unavailable.

---

### 3. No retry budget or iteration counter

**What happens:** Each workstream has `max_iterations: 3` set in the plan model, but this
field is never used. The harness runs exactly once: grounding → gates → fallback (one pass)
→ gates → synthesis. If the second gate pass also fails, synthesis runs on weak evidence
without another iteration.

**Impact:** The `max_iterations` field is dead configuration. The harness has no bounded
retry loop. Three iterations would allow: (1) grounding, (2) targeted fallback for weak
pillars, (3) a final targeted search pass before synthesis.

---

### 4. Final synthesis is a single point of failure

**What happens:** `FinalSynthesisGenerator.synthesize()` is called at the end of the pipeline.
If it raises an exception (API timeout, token limit exceeded, rate limit), the entire run
is marked `failed` even though all prior stages — grounding, documents, scoring — completed
successfully.

**Impact:** A user who waits 5+ minutes for research to complete gets a failure with no
recoverable output. The scorecard, evidence, and pillar assessments are all lost from the
user's perspective even though they exist in the database.

**Fix:** Persist intermediate results (scorecard, evidence, pillar assessments) to
`result_json` before calling synthesis. If synthesis fails, mark the run `completed_partial`
and return what exists.

---

### 5. Evidence alignment score has sharp binary boundaries

**What happens:** The deterministic alignment score adds points on 6 dimensions (pillar match,
signal validity, confidence, content, keyword, goal fit). Each dimension is binary — you
either get the full points or none. A fact with score 0.64 is rejected; a fact with 0.65 passes.

**Impact:** Borderline-relevant facts are lost. A fact mentioning "return on equity" for
the Financial Engine pillar gets rejected if the signal name is slightly off (e.g.,
"Shareholder Returns" instead of "Profitability"). No partial credit for partially relevant evidence.

**Downstream:** Low aligned-fact counts trigger gate failure and fallback even when the
evidence is qualitatively useful.

---

### 6. Cross-pillar evidence duplication

**What happens:** If the same fact (e.g., "Revenue grew 20% YoY") appears in both the
Financial Engine and Valuation evidence lists, it is counted independently in each.
The gate evaluates both pillars' evidence separately, and the same fact inflates both counts.

**Impact:** A run with one strong source can appear to have sufficient evidence across
multiple pillars. Gate results are overoptimistic. Synthesis may cite the same fact twice
in different pillar sections.

---

### 7. Document ranking can reject valid primary sources

**What happens:** `_rank_document_candidates()` rejects SEC filings that don't contain the
company name or ticker in the URL, title, or snippet. This is intended to filter out
unrelated filings, but the check is too strict: a valid 10-K whose SEC URL and snippet
don't explicitly repeat the company name (common for small-caps) is rejected.

**Impact:** Financial Engine and Management pillars may fail their primary-document gate
requirement not because documents don't exist, but because the ranking filter discarded them.

---

### 8. Table extraction is fragile

**What happens:** Tables are detected by splitting text on `|`, `\t`, or 2+ spaces.
Confidence is computed as a formula of row count, period columns, and classification.
Tables with confidence below 0.35 are dropped before evidence extraction.

**Impact:** Financial tables in non-standard formats (HTML tables converted to text,
merged cells, prose-style tables) are missed. Scanned PDFs are not supported (no OCR).
A 3-row income statement with no period header gets confidence 0.35 and may be dropped.

---

### 9. The LLM alignment judge is costly and inconsistent

**What happens:** When enabled, the alignment judge calls GPT-4o-mini for every evidence
fact. The judge's `verdict` overrides the deterministic score: `accepted` boosts, `rejected`
caps at 0.2, `needs_review` applies a soft cap. If the OpenAI call fails, the error is
silently caught and the deterministic score is used.

**Impact:** API failures produce silent accuracy degradation with no record in the trace.
The judge is also called per-fact with no batching, making it expensive on runs with many
evidence candidates. Cost grows linearly with evidence count.

---

### 10. The harness is not truly agentic

**What happens:** The `ResearchToolFacade` exposes ~10 methods. These are called in a
hardcoded sequence in `runner.py`. The planner generates a plan, but the plan does not
drive tool selection at runtime — the runner always calls the same tools in the same order
regardless of what the plan says about required evidence or search focus.

**Impact:** The plan is advisory, not executable. Adding a new tool requires changing
`runner.py`, not just registering a tool. There is no tool registry, no tool dispatch, and
no mechanism for the plan to say "use the SEC EDGAR tool for this workstream."

---

## Summary Table

| Area | Status | Severity |
|------|--------|----------|
| Plan adaptation mid-run | ❌ Not implemented | High |
| Silent grounding degradation | ❌ No warning | Medium |
| Retry iteration counter | ❌ Dead config | Medium |
| Synthesis single point of failure | ❌ Loses all prior work | High |
| Alignment score binary boundaries | ⚠️ Too strict | Medium |
| Cross-pillar evidence duplication | ⚠️ Inflates counts | Low |
| Document ranking too strict | ⚠️ Rejects valid sources | Medium |
| Table extraction fragile | ⚠️ Misses non-standard tables | Medium |
| LLM judge cost + inconsistency | ⚠️ Silent on API failure | Medium |
| Not truly agentic (hardcoded sequence) | ⚠️ By design for now | Low (private beta) |

---

## Recommended Next Steps (Prioritized)

**P0 — Fix before paid beta**
1. Persist intermediate results before synthesis so partial results are recoverable.
2. Log a startup warning and emit a gate gap when grounding key is missing.

**P1 — Improve quality**
3. Implement one additional iteration loop: if gates fail post-fallback, run a second
   targeted search pass using the gap reasons as new query hints.
4. Soften alignment scoring: add partial credit tiers (0.0 / 0.4 / 0.7 / 1.0) instead
   of binary per dimension.
5. Batch LLM alignment judge calls (one prompt, N facts) to reduce cost and latency.
6. Deduplicate evidence facts across pillars before gate evaluation.

**P2 — Towards a real agentic loop**
7. Make the plan executable: runner reads workstream.search_focus and selects tools
   accordingly rather than always calling the same sequence.
8. Add a tool registry so new tools (EDGAR API, earnings call transcripts, options data)
   can be added without changing runner.py.
