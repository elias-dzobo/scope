# Final Research Synthesis Backend + UI Plan

## Summary

Add a new LLM-only final synthesis layer after scoring and evidence assembly. The goal is to transform the existing technical research output into a user-facing investment memo that still preserves financial rigor, but explains the company and each pillar in language a non-finance user can understand.

The UI should render this `finalSynthesis` as the primary completed report. Existing scorecard, pillar assessments, evidence, and sources remain available for expanded pillar detail, but the top-level experience should no longer read like raw scoring artifacts.

## Key Changes

- Add a `FinalResearchSynthesis` result object to the completed run payload.
  - Keep existing fields additive and backward-compatible.
  - New field: `finalSynthesis`.
  - Persist it inside `result_json` in `storage/research_runs.db`.
  - Include it in `GET /research-runs/{run_id}/results`.

- Generate synthesis with an LLM-only step after scorecard creation.
  - Add a research-core synthesis module, e.g. `research_core/synthesis/final_synthesis.py`.
  - Input: company name, ticker, scorecard, pillar assessments, top evidence, top sources, grounded/document facts.
  - Output: structured JSON matching `FinalResearchSynthesis`.
  - Retry policy: retry the synthesis call longer before failing, with 3 attempts and backoff around 2s, 5s, 10s.
  - If all attempts fail, mark the research run as failed with a clear synthesis error. Do not silently fall back to raw scorecard.

- Recommended `FinalResearchSynthesis` shape:
  - `companySnapshot`: what the company does, how it makes money, why investors care.
  - `investmentTakeaway`: plain-English recommendation summary.
  - `recommendationRationale`: 3-5 user-facing reasons for the rating.
  - `mainRisks`: 3-5 plain-English risks or watch items.
  - `pillarTakeaways`: one item per pillar with:
    - `pillarName`
    - `score`
    - `plainEnglishSummary`
    - `position`
    - `whyItMatters`
    - `supportingPoints`
    - `watchItems`
    - `technicalDetails`
  - `bottomLine`: short closing memo.
  - `sourceNote`: concise explanation that conclusions are evidence-backed from filings, grounded sources, and parsed documents.

- Prompt requirements:
  - Explain financial terms naturally without removing them.
  - Avoid phrases like “evidence hits” in user-facing prose.
  - Do not invent facts not present in evidence/scorecard.
  - Include company business description when evidence supports it.
  - Surface conflicts clearly, e.g. strong fundamentals but weak technical trend.
  - Keep each pillar understandable first, technical second.

- UI sync:
  - Update `apps/web/src/types/api.ts` with `FinalResearchSynthesis`.
  - Update `AnalysisDashboard` to render `finalSynthesis` first.
  - Top report layout:
    - company snapshot
    - investment takeaway
    - recommendation/score/confidence
    - why this could work
    - what could go wrong
    - six pillar summaries
  - Pillars should show plain-English summary by default.
  - Expanded pillar details should show technical details, evidence excerpts, and sources.
  - If opening an old run without `finalSynthesis`, show the existing scorecard-based layout as compatibility mode.

## Implementation Details

- Backend flow:
  - `CompanyResearchRunner` builds scorecard as it does now.
  - Immediately after scorecard creation, call `FinalSynthesisGenerator`.
  - Add `final_synthesis` into the summary returned by the runner.
  - Update `_build_result_payload(...)` to map `summary["final_synthesis"]` to API key `finalSynthesis`.

- Storage:
  - SQLite `result_json` stores final synthesis with the rest of the completed payload.
  - No new table is needed for this pass.
  - Optional artifact: write `storage/artifacts/{TICKER}/synthesis/final_synthesis.json` for audit/debugging.

- UI behavior:
  - Completed report should not show intermediate process artifacts.
  - Do not show harness trace, workstream plan, document index, or diagnostics in the main UI.
  - Keep sources/evidence visible only inside expanded pillar detail.

## Test Plan

- Backend unit tests:
  - synthesis prompt receives scorecard, pillar assessments, evidence, and sources.
  - valid LLM JSON becomes `FinalResearchSynthesis`.
  - invalid JSON retries.
  - repeated synthesis failure fails the run.
  - result payload includes `finalSynthesis`.

- API tests:
  - completed run result includes existing keys plus `finalSynthesis`.
  - old result payloads without `finalSynthesis` still deserialize.
  - failed synthesis produces a run failure state with useful error message.

- Frontend tests/build:
  - `npm run build` passes.
  - dashboard renders `finalSynthesis` when present.
  - dashboard falls back to old scorecard layout when `finalSynthesis` is absent.
  - pillar expanders reveal technical details/evidence/sources without making the default view too technical.

- Regression:
  - `uv run python -m pytest tests/test_*.py`
  - `uv run python scripts/export_openapi.py`
  - `cd apps/web && npm run build`

## Assumptions

- Final synthesis is LLM-only.
- The system retries synthesis longer, then fails the run if synthesis cannot be produced.
- The UI should show final research synthesis only, not intermediate harness process details.
- Existing result payload fields remain for compatibility, audit, and expanded details.
