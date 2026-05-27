# Scoring System — Implementation Strategy

**Status:** Recommended  
**Date:** 2026-05-22

---

## Guiding Principles

1. Fix correctness bugs first. The circular confidence formula and unconstrained LLM revision are correctness issues, not polish.
2. Each phase is independently releasable. No phase requires the next to function correctly.
3. The `scorecard_source` field in the scorecard response exposes which path was taken — use it for monitoring.
4. Maintain backward-compatible schema throughout. Existing API consumers should not need changes until Phase 3 adds new fields.

---

## Phase 0 — Critical Bug Fixes (1–2 days)

### 0.1 Fix Circular Confidence Formula

**File:** `packages/research-core/src/research_core/scoring/main.py`  
**Function:** `_extract_pillar_evidence_deterministic()`

Replace:
```python
confidence = min(1.0, 0.45 + (0.1 * len(signal_hits)))
```

With:
```python
def _evidence_confidence_deterministic(*, body: str, source_title: str, url: str = "") -> float:
    base = 0.35
    combined = f"{source_title}\n{url}".lower()
    if any(t in combined for t in ("sec.gov", "10-k", "annual report", "investor", "earnings")):
        base += 0.20
    if re.search(r"\b\d+(?:\.\d+)?%\b|\b\$\s?\d|\b\d+(?:\.\d+)?\s?(?:billion|million|bn|m)\b", body.lower()):
        base += 0.15
    if len(body) > 500:
        base += 0.05
    return min(0.75, base)
```

**Acceptance criteria:**
- A primary SEC filing excerpt scores ≥ 0.55 regardless of keyword count.
- A short snippet with one keyword scores ≤ 0.40.

### 0.2 Add LLM Revision Bounds

**File:** `packages/research-core/src/research_core/scoring/main.py`  
**Function:** `_build_stock_scorecard_llm()`

After the LLM returns a scorecard, apply `_apply_score_bounds()` and `_constrain_recommendation()` (see System Design § 6.1 and § 6.2) before returning.

**Acceptance criteria:**
- An LLM that returns `"Good Buy"` when the deterministic baseline is `"Avoid"` is constrained to at most `"Hold / Not Attractive Now"`.
- A pillar score that the LLM inflates by 30 points is clamped to +15.

### 0.3 Fix Generic Invalidation Conditions

Replace the three hardcoded boilerplate strings with `_build_invalidation_conditions()` (see System Design § 5.3).

**Acceptance criteria:**
- A company with no moat evidence does not have a moat-specific invalidation condition.
- A company with strong moat evidence has an invalidation condition specific to the moat gaps found.

### 0.4 Expand Test Coverage

Create `tests/test_scoring_evals.py` with the full test suite (see test plan below).

---

## Phase 1 — Expanded Signal Vocabulary (3–5 days)

### 1.1 Introduce `SignalDefinition`

**File:** `packages/research-core/src/research_core/scoring/main.py`

Add `SignalDefinition` dataclass. Expand all 12 signal keyword lists to 15–25 terms per signal, with explicit negative keyword lists.

**Migration:** The scoring formula stays unchanged. Only the keyword vocabulary is expanded. Existing scores will generally increase (more keywords = more matches), so calibrate thresholds downward in Phase 2.

### 1.2 Negation-Aware Matching

Implement `_has_negative_match(text, negative_keywords, match_position, window=30)`:
- Extract a 30-word window around the keyword match.
- If a negative keyword appears in that window, subtract 0.10 from confidence (floor 0.15).

### 1.3 Add Pillar Signal Coverage Tests

Add test cases to `tests/test_scoring_evals.py` that verify:
- Each signal matches its synonyms (e.g., "return on invested capital" matches Profitability).
- Each signal's negative keywords reduce confidence.
- No false positives on known negation phrases.

---

## Phase 2 — Scoring Formula Calibration (1 week)

### 2.1 Score Distribution Analysis

Run the updated scoring system against 50 manually-selected public companies. Plot the distribution of:
- Overall scores.
- Per-pillar scores.
- Recommendation frequency by category.

Expected finding: scores will cluster in the 40–65 range. Use this to calibrate thresholds.

### 2.2 Update Pillar Scoring Formula

Apply the revised formula from System Design § 3.1:
- Coverage weight: 35 → 30.
- Quality weight: 35 → 40.
- Density cap: 8 → 10.
- Diversity cap: 4 → 5.

Apply the graduated evidence cap from System Design § 3.2.

### 2.3 Recalibrate Recommendation Thresholds

Using the score distribution data, set thresholds so:
- ~5% of scored companies reach "Good Buy" tier.
- ~25% reach "Watch / Accumulate on Weakness" tier.
- ~40% fall in "Hold" or "Cautious" categories.
- ~30% are "Insufficient Data" or "Avoid".

This distribution is intentional: a research tool that says "Good Buy" 40% of the time is not a useful signal.

### 2.4 Partial Run Confidence Penalty

Implement the partial-run penalty from System Design § 4.3.

---

## Phase 3 — Industry-Adjusted Weights (1–2 weeks)

### 3.1 Sector Lookup Table

Add `SECTOR_BY_TICKER: dict[str, str]` for the top 200 tickers by trading volume. Sectors: Technology, Healthcare, Financials, Consumer Staples, Consumer Discretionary, Energy, Industrials, Materials, Utilities, Real Estate, Communication Services.

### 3.2 Sector-Specific Pillar Weights

Add `PILLAR_WEIGHTS_BY_SECTOR` (see System Design § 4.2). The `build_stock_scorecard()` function accepts an optional `sector: str | None` parameter and selects the appropriate weight table.

### 3.3 Schema Addition

Add `sector` and `applied_weight_table` to the `StockScorecard` schema so the API response exposes which weight table was used.

---

## Phase 4 — Semantic Signal Matching (2–3 weeks)

### 4.1 Signal Anchor Sentences

Define 3–5 "anchor sentences" per signal that exemplify strong evidence. Embed them at startup using `text-embedding-3-small`.

### 4.2 Hybrid Evidence Extraction

In `_extract_pillar_evidence_deterministic()`, supplement keyword matching with cosine similarity to anchor sentence embeddings:
- Keyword match AND cosine similarity ≥ 0.75 → `confidence += 0.10`.
- Cosine similarity ≥ 0.80 but no keyword match → still counts as a hit with `confidence = 0.45`.

This closes the synonym blindness gap without requiring a full LLM extraction for every document.

### 4.3 Cache Embeddings

Cache document embeddings in Redis (keyed by `sha256(body[:2000])`) to avoid re-embedding the same text on every scoring run.

---

## Phase 5 — Automated Score Regression (ongoing)

### 5.1 Golden Scorecard Set

Maintain a file `tests/scoring_golden_cases.json`:
```json
[
  {
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "expected_recommendation_range": ["Watch / Accumulate on Weakness", "Wait for Dip", "Good Buy"],
    "expected_overall_score_min": 65,
    "expected_financial_score_min": 70
  },
  ...
]
```

### 5.2 Weekly Regression Run

Run the golden scorecard set weekly against the live scoring system (with real evidence). Alert (Slack/email) if:
- Any company's recommendation changes by more than one tier.
- The overall score changes by more than 10 points.

### 5.3 Score Calibration Review

Quarterly review comparing Scope recommendations against analyst consensus for the previous quarter. Track precision/recall at each recommendation tier.

---

## Rollout Plan

| Phase | Duration | Risk | Feature Flag |
|-------|----------|------|-------------|
| 0 — Bug fixes | 1–2 days | Low | Off (always on) |
| 1 — Expanded vocab | 3–5 days | Low | `SCORING_EXPANDED_SIGNALS=true` |
| 2 — Formula calibration | 1 week | Medium | `SCORING_CALIBRATED_V2=true` |
| 3 — Industry weights | 1–2 weeks | Medium | `SCORING_SECTOR_WEIGHTS=true` |
| 4 — Semantic matching | 2–3 weeks | Medium | `SCORING_SEMANTIC_SIGNALS=true` |
| 5 — Regression | Ongoing | Low | N/A |
