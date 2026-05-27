# System Design Evaluation

An honest critique of the current Scope architecture — what is well-designed,
what is over-engineered for the current stage, and what is structurally wrong.

---

## What Is Well-Designed

**Typed pipeline contracts.** Every stage produces a Pydantic model (`ResearchPlan`,
`GroundedResearchResult`, `ParsedDocument`, `QualityGateResult`, `FinalResearchSynthesis`).
The result shape is stable, validated in CI, and can be reasoned about independently
of the code that produces it. This is the right foundation.

**Clean domain boundary.** `research-core` and `provider-integrations` have no
dependency on FastAPI. The API layer is a thin transport wrapper. This means the
research pipeline can be tested, improved, and replaced without touching routing code.

**Graceful degradation.** Planning falls back to a deterministic plan if OpenAI is
unavailable. Document extraction falls back to keywords if LLM extraction fails.
The pipeline always reaches synthesis. This is the right default posture for a research
system that depends on multiple external APIs.

**Observable execution.** Every stage emits progress to the database. The frontend
can show the current stage, substep, and activity count in real time (via polling).
The JSONL trace file per run is useful for debugging.

**Deterministic scoring.** Pillar scoring and scorecard generation use no LLM randomness.
Given the same evidence, scores are reproducible. This is critical for a product that
makes investment-adjacent recommendations.

**Durable worker design.** The DB-backed lease + heartbeat model is the correct
approach for long-running research jobs. It survives API restarts, worker crashes,
and Fly machine suspensions cleanly.

---

## What Is Over-Engineered for Private Beta

### Two execution backends maintained in parallel

`orchestration.py` (in-process) and `worker.py` (durable) both implement full job
execution logic, retry handling, and DB state transitions. They share almost no code.
This means bug fixes and improvements must be made twice. In practice, private beta
should run only durable mode. In-process mode adds maintenance cost with no production
benefit.

**Verdict:** Delete `orchestration.py` and `InProcessOrchestrator`. Run durable mode
in all environments. SQLite + durable worker is perfectly fast enough for local dev.

---

### GraphRAG memory before there are users

The memory graph (`memory_nodes`, `memory_edges`, `memory_chunks`) is a full GraphRAG
implementation with typed nodes, directed edges, and text chunks. It is built and indexed
after every research run. At private beta with no paying users, this system has never
been queried in production. The advisor currently retrieves memory context, but it is not
clear that the graph structure (as opposed to flat search over run summaries) adds value
at this scale.

**Verdict:** Keep the advisor and memory tables but simplify retrieval. Flat text search
over `memory_chunks` is sufficient for private beta. Reserve graph traversal for when
there are enough runs per user to justify it.

---

### Billing tables with no billing

`user_entitlements`, `credit_reservations`, `usage_ledger` — eight migration steps.
This schema anticipates a Stripe integration and credit-per-research model. There is
currently no billing. These tables are populated but never read by any product feature.

**Verdict:** Useful as future scaffolding. Do not invest time here until Stripe is
actually being integrated.

---

### Three compatibility shim layers

Root-level `research_core/`, `scope_api/`, and `provider_integrations/` directories
exist only to bridge old import paths. They add namespace ambiguity, confuse static
analysis tools, and create a second surface area for import errors. `src/` adds a third.

**Verdict:** Phase 9 of the refactor plan removes these. That should be accelerated —
it is blocking reliable static analysis and IDE tooling.

---

### Frontend polling at 3 seconds

The frontend polls `GET /research-runs/{id}` every 3 seconds while a run is in progress.
A 5-minute research run generates ~100 unnecessary HTTP roundtrips. Each poll hits the
database to read `status`, `progress`, and `current_stage`. Under load (10 concurrent
research runs), that is 1,000 DB reads per minute for progress data alone.

**Verdict:** Replace with Server-Sent Events (SSE). FastAPI supports SSE natively.
The worker writes progress events to `run_events`; the SSE endpoint streams those to
the client. This eliminates polling overhead and enables streaming partial results.

---

## What Is Structurally Wrong

### 1. The plan is decorative, not executable

The `ResearchPlan` contains workstreams with `search_focus`, `required_evidence`, and
`max_iterations`. None of these fields drive runtime behavior:

- `search_focus` is never injected into Gemini prompts or search queries.
- `required_evidence` is used by quality gates after the fact but not during search.
- `max_iterations` is never read by the runner.

The planner generates a plan. The runner ignores it and calls the same sequence of tools
regardless of what the plan says. Planning is wasted LLM cost.

**Impact:** The harness cannot adapt to company-specific context. A pre-IPO company
(no public financials) goes through the same pipeline as a large-cap with 20 years of
SEC filings. An emerging-market stock with no English-language coverage gets the same
English search queries as a US-listed company.

---

### 2. Final synthesis is a single point of failure

All prior stages (grounding, documents, scoring) complete successfully and write to
the database. The synthesis call is then made as the very last step. If it fails
(OpenAI timeout, rate limit, context length exceeded), the entire run is marked
`failed` and the user sees nothing.

The scorecard, evidence, pillar assessments, and source list are already in memory
at this point but are not persisted to `result_json` until synthesis also succeeds.

**Impact:** Users who wait 5+ minutes get a failure with no recoverable output.
This is the single most damaging UX failure mode in the system.

---

### 3. No iteration loop — gates have no path to re-plan

The quality gate system detects specific deficiencies (wrong pillar, thin content,
missing primary source, stale data). But the only response to a gate failure is
a coarse fallback: run the legacy deterministic pipeline for the 2 weakest workstreams.
The gate's specific gap reasons are never fed back into the search or grounding stage.

If Valuation gate fails because "no P/E multiple found in any source," the harness
cannot generate a new query like `"[ticker] trailing P/E multiple analyst consensus"`.
It runs the same legacy pipeline that was already tried and found insufficient.

**Impact:** Gate failures result in synthesis over weak evidence rather than better evidence.

---

### 4. Evidence alignment scoring is too binary

The deterministic alignment score adds full points or none per dimension. A fact with
score 0.64 is rejected; one with 0.65 passes. The LLM judge can override this but adds
per-fact API calls with no batching.

The threshold itself is fixed (`0.65`) with no workstream-specific tuning. A Macro &
Industry pillar (qualitative, hard to cite numerically) is judged by the same threshold
as Financial Engine (quantitative, numeric values expected).

**Impact:** Qualitative pillars (Macro, Moat) systematically fail alignment gates and
trigger unnecessary fallbacks, adding latency and cost.

---

### 5. Silent degradation is invisible to operators

Three critical failures are currently silent:

| Failure | Current behavior |
|---------|-----------------|
| `GOOGLE_API_KEY` missing | Grounding returns `status=unavailable`, pipeline continues |
| OpenAI doc extraction API error | Falls back to keywords, no warning emitted |
| LLM alignment judge API error | Falls back to deterministic score, no trace entry |

None of these write to `run_events`. An operator reading the run's event log has no
indication that any of these occurred. The quality gate result may report `passed=True`
on evidence that was entirely keyword-extracted with no LLM enrichment.

---

### 6. Document table extraction is too fragile for the core use case

Financial tables in annual reports are the most valuable evidence for Financial Engine,
Management, and Valuation pillars. The current extractor uses regex-split on `|`, `\t`,
or 2+ spaces. This fails on:
- HTML tables converted to prose text (common in rendered PDF → text conversions)
- Tables with merged cells or irregular spacing
- Multi-line row entries

A significant portion of annual report financial data is never extracted, leading to
systematic gate failures on the three most important pillars.

---

### 7. Search provider chain has no quality signal

The search chain (Exa → Tavily → SerpAPI → DDG) returns the first provider that succeeds.
There is no comparison of result quality across providers. A Exa result with 3 low-relevance
URLs is treated the same as a Tavily result with 8 high-relevance URLs.

There is also no deduplication between search results and Gemini grounding sources.
The same URL can appear in both, inflating source counts in quality gates.

---

## Summary

| Area | Verdict |
|------|---------|
| Pipeline contracts (Pydantic) | ✅ Keep |
| Domain boundary (API vs research-core) | ✅ Keep |
| Graceful degradation | ✅ Keep |
| Deterministic scoring | ✅ Keep |
| Durable worker | ✅ Keep, remove in-process mode |
| In-process orchestrator | ❌ Remove |
| GraphRAG complexity | ⚠️ Simplify for now |
| Billing tables | ⚠️ Keep schema, don't invest |
| Compatibility shims | ❌ Accelerate Phase 9 removal |
| Frontend polling | ⚠️ Replace with SSE before public launch |
| Plan executability | ❌ Fix — plan must drive tool selection |
| Synthesis as final step | ❌ Fix — persist intermediate results first |
| Gate → re-plan loop | ❌ Missing entirely |
| Alignment score thresholds | ⚠️ Per-pillar tuning needed |
| Silent degradation | ❌ Fix — all fallbacks must emit events |
| Table extraction | ⚠️ Needs a more robust HTML table parser |
| Search deduplication | ⚠️ Cross-source URL dedup needed |
