# Scoring System — Honest Evaluation

**Status:** Pre-production  
**Date:** 2026-05-22  
**Scope:** `packages/research-core/src/research_core/scoring/main.py`

---

## Executive Summary

The scoring system delivers a complete, deterministic baseline that is structurally sound and fails gracefully. The core problem is that the signal vocabulary, scoring weights, and recommendation thresholds are all **hardcoded constants that have never been validated against real investment outcomes**. The LLM synthesis overlay adds flexibility but introduces unconstrained score inflation risk. The system will produce confident-sounding `"Good Buy"` recommendations for companies with 3–4 evidence facts matching simple keywords, which is not enough evidence for an investment-grade signal.

---

## 1. Signal Vocabulary Weaknesses

### 1.1 12 Keywords Per Signal is Too Narrow
**Severity: High**

Each signal is defined by 4–7 lowercase keywords. For example:
- `"Profitability"`: `["roic", "margin", "ebitda", "eps", "profit"]`
- `"Competitive Advantage"`: `["brand", "patent", "network effect", "cost advantage"]`

**Problems:**
- **Synonym blindness.** An article about "return on invested capital" does not match `"roic"`. "Operating leverage" does not match `"margin"`. "Intellectual property" does not match `"patent"`. In practice, 30–50% of relevant evidence will be missed because the keyword is absent from the exact text form.
- **False positives.** The keyword `"profit"` matches `"non-profit"`, `"profit-taking"`, or `"profit warning"` — all of which have the opposite meaning. There is no negation matching.
- **Language-insensitive.** The matching is `.lower()` substring — `"ebitda"` matches `"post-ebitda-adjusted"` correctly, but there is no stemming or lemmatization, so `"profitable"` does not match `"profitability"`.
- **No domain expansion.** As new financial terminology emerges (e.g., "AI monetization", "DOGE cost savings", "tariff headwind"), the keyword lists do not self-update.

### 1.2 Signal Coverage is Binary Per Document
**Severity: Medium**

`_extract_signal_hits()` returns a binary hit/miss per document per signal. There is no partial credit for documents that contain weaker keyword variants. A document discussing ROIC at length with the word "returns on capital" gets zero signal credit.

### 1.3 No Relevance Weight Within a Signal
**Severity: Low-Medium**

All keywords within a signal are equally weighted. A mention of "profit" in a passing sentence carries the same signal strength as a dedicated section with an EBITDA breakdown. There is no distinction between incidental mention and primary evidence.

---

## 2. Pillar Scoring Formula Weaknesses

### 2.1 Formula Coefficients are Unvalidated
**Severity: High**

The pillar score formula:
```
raw_score = (35 × coverage_ratio) + (20 × density_ratio) + (10 × diversity_ratio) + (35 × avg_confidence)
```

These weights (35, 20, 10, 35) were chosen heuristically. There is no empirical evidence that this formula correlates with actual investment outcomes. In particular:

- **Coverage (35%) rewards breadth over depth.** A pillar with thin evidence on both signals scores higher than a pillar with deep evidence on one signal and a gap on another. This penalizes companies with specialized business models.
- **Density cap at 8 (density_ratio = evidence_count / 8).** A pillar with 12 evidence facts scores the same as one with 8. This discourages evidence quality improvements past the cap.
- **Source diversity cap at 4 (diversity_ratio = source_diversity / 4).** Three independent confirming sources score only 75% of the maximum possible diversity score. This is a low bar.
- **Average confidence (35%).** The deterministic evidence extractor assigns `confidence = 0.45 + (0.1 × num_signal_hits_in_doc)`. For a document hitting 2 signals, `confidence = 0.65`. This formula means a document with more keyword matches gets higher confidence, regardless of whether those matches are meaningful.

### 2.2 Evidence Cap at 50 for < 2 Facts is Too Conservative
**Severity: Medium**

When a pillar has fewer than 2 evidence facts, the score is capped at 50 (`if evidence_count < 2: raw_score = min(raw_score, 50)`). This means a pillar with 1 very-high-confidence fact from a primary filing cannot score above 50. A pillar with 0 facts scores 0 (correct). The cap at 50 for 1 fact means the boundary between `"Insufficient Data"` (evidence < 2 OR confidence < 0.35) and `"Neutral"` (score >= 60) requires exactly 2 facts — creating a sharp cliff at the boundary.

### 2.3 Pillar Classification Thresholds are Magic Numbers
**Severity: Medium**

```python
if score >= 75: category = "Bullish"
elif score >= 60: category = "Neutral"
elif score >= 45: category = "Cautious"
else: category = "Weak"
```

These thresholds (75, 60, 45) are not derived from any distribution of actual pillar scores. In practice, the formula tends to produce scores in the 40–65 range for companies with reasonable evidence, meaning most pillars cluster in `"Neutral"` / `"Cautious"` and rarely reach `"Bullish"`.

---

## 3. Pillar Weights Weaknesses

### 3.1 Weights are Unvalidated Constants
**Severity: High**

```python
PILLAR_WEIGHTS = {
    "Financial Engine": 0.25,
    "Economic Moat": 0.20,
    "Macro & Industry": 0.15,
    "Management & Capital Allocation": 0.15,
    "Valuation": 0.15,
    "Technical Analysis": 0.10,
}
```

These weights have not been validated against investment performance data. The choice to weight Financial Engine at 25% and Technical Analysis at 10% is reasonable as a prior but untested. For different investment styles (momentum, value, growth) the optimal weights would differ significantly.

### 3.2 No Industry or Market-Cap Adjustment
**Severity: Medium**

A growth-stage biotech company and a mature consumer staple company are scored using the same pillar weights. For the biotech, moat and macro signals are far more important than technicals; for the consumer staple, cash generation and management quality dominate. There is no per-company or per-sector weight adjustment.

### 3.3 Missing Pillars Are Normalized Away
**Severity: Low-Medium**

When only a subset of pillars are scored (e.g., user selects 3 pillars), the weights are renormalized:
```python
weight_sum = sum(available_weights.values()) or 1.0
normalized_weights = {pillar: weight / weight_sum for pillar in pillar_scores}
```

This means a 3-pillar run can produce the same overall score as a 6-pillar run, which is misleading. A partial research run should signal lower confidence, not equivalent confidence.

---

## 4. Recommendation Logic Weaknesses

### 4.1 Recommendation Tree is a Hardcoded Decision Table
**Severity: High**

```python
if pillars_with_sufficient_data < 3:
    recommendation = "Insufficient Data"
elif overall_score >= 75 and financial_score >= 70 and moat_score >= 65:
    if valuation_status == "Overvalued": recommendation = "Wait for Dip"
    elif technical_state == "Bearish Trend": recommendation = "Watch / Accumulate on Weakness"
    else: recommendation = "Good Buy"
elif overall_score >= 62:
    if valuation_status == "Overvalued": recommendation = "Hold / Not Attractive Now"
    else: recommendation = "Watch / Accumulate on Weakness"
else:
    recommendation = "Avoid"
```

Thresholds (75, 70, 65, 62) are arbitrary. A score of 74 vs 75 produces a different recommendation despite being effectively the same evidence quality. There is no confidence interval or smooth transition zone.

### 4.2 "Good Buy" Too Easy to Achieve
**Severity: High**

A company needs `overall_score >= 75`, `financial_score >= 70`, `moat_score >= 65`, and `valuation_status != "Overvalued"` and `technical_state != "Bearish Trend"`. Given that:
- The scoring formula allows `overall_score = 75` with 8 evidence facts across 6 signals at avg_confidence 0.65.
- A single document hitting 4 keywords produces a `confidence=0.85` fact.
- The formula rewards breadth over depth.

This means a "Good Buy" can be generated from ~8 keyword matches across 6 signals in a small number of documents. This is not a high bar for an investment recommendation.

### 4.3 Invalidation Conditions are Generic Boilerplate
**Severity: Medium**

The `invalidation_conditions` field is always:
```python
[
    "Financial Engine score drops below 60 on next earnings cycle.",
    "Valuation remains overvalued while momentum weakens.",
    "Key moat signals deteriorate across recent filings.",
]
```

These are generated regardless of the actual evidence. A company with zero moat evidence gets a moat invalidation condition anyway. This is boilerplate, not analysis.

---

## 5. LLM Scorecard Synthesis Weaknesses

### 5.1 Unconstrained Score Revision
**Severity: High**

`_build_stock_scorecard_llm()` passes the deterministic baseline to `gpt-4o-mini` and instructs it to "adjust scores or recommendation only when the evidence supports it." The system prompt does not prevent the LLM from raising a score of 45 to 85 based on general reasoning about the company type. There is no constraint on the magnitude of score adjustment.

**Impact:** LLM synthesis can inflate scores and upgrade recommendations for well-known companies (NVDA, AAPL) based on training data, not the supplied evidence.

### 5.2 No Baseline Comparison Assertion
**Severity: Medium**

After the LLM returns a revised scorecard, there is no check that:
- The LLM score is within a reasonable delta of the deterministic baseline (e.g., ±20 points per pillar).
- The recommendation did not jump 2+ levels (from `"Avoid"` to `"Good Buy"`).
- The confidence did not increase when evidence was thin.

### 5.3 LLM Evidence Exposure is Truncated
**Severity: Low**

`_compact_evidence_for_scorecard()` trims evidence to 5 facts per pillar, each excerpt to 280 characters. For pillars with 15+ facts, the LLM sees only 33% of the evidence. This can cause the LLM to over-index on whichever 5 facts happen to be at the top of the list.

---

## 6. Evidence Extraction Weaknesses

### 6.1 Deterministic Extractor Confidence Formula is Circular
**Severity: High**

```python
confidence = min(1.0, 0.45 + (0.1 * len(signal_hits)))
```

The confidence score increases with the number of signal keyword matches in a document. But the signal keywords and the confidence formula use the same input — a document with more keywords is more confident. This is circular: the score rewards documents that contain more keywords, not documents that contain more meaningful evidence.

**Example:** A press release mentioning "EBITDA", "margin", "profit", "eps", "buyback", "dividend" will have `signal_hits=6` across two pillars and `confidence=1.05 → 1.0`. A single authoritative primary filing with a detailed ROIC analysis using the term "return on invested capital" gets `confidence=0.45` (1 signal hit).

### 6.2 LLM Evidence Extractor Has No Retry Logic
**Severity: Medium**

`_extract_pillar_evidence_llm_chunk()` wraps a `gpt-4o-mini` call. On failure it falls through to the deterministic extractor silently. There is no partial retry (only one attempt before fallback). This means transient API errors during a single chunk silently degrade the evidence quality for that chunk.

---

## 7. Priority Ranking

| # | Issue | Severity | Effort |
|---|-------|----------|--------|
| 1 | Keyword synonym blindness (30–50% evidence miss rate) | High | Medium |
| 2 | "Good Buy" achievable with minimal evidence | High | Medium |
| 3 | LLM score revision is unconstrained | High | Low |
| 4 | Recommendation thresholds are magic numbers | High | Medium |
| 5 | Deterministic confidence formula is circular | High | Low |
| 6 | Pillar weights unvalidated for investment outcomes | High | High |
| 7 | Invalidation conditions are always the same boilerplate | Medium | Low |
| 8 | Pillar classification thresholds create score clusters | Medium | Low |
| 9 | Missing pillars normalized away (inflates confidence) | Medium | Low |
| 10 | LLM evidence exposure truncated to 5 facts | Low | Low |

---

## 8. What is Working Well

- The deterministic baseline always runs before LLM synthesis — a fallback path always exists.
- The `evidence_count < 2 → cap at 50` heuristic correctly prevents high-confidence scores from thin evidence.
- Source diversity is tracked and rewarded, creating a mild incentive for multi-source evidence.
- The `pillars_with_sufficient_data < 3 → "Insufficient Data"` gate prevents recommendations with no supporting evidence across most pillars.
- LLM scorecard failures fall back to the deterministic baseline — the pipeline never crashes on LLM errors.
- The `scorecard_source` field (`"deterministic"`, `"llm_assisted"`, `"deterministic_fallback"`) provides transparency about which path was taken.
