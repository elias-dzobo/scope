# Scoring System — System Design

**Status:** Recommended Architecture  
**Date:** 2026-05-22  
**Supersedes:** Current `scoring/main.py` implementation

---

## 1. Design Goals

1. **Evidence quality over keyword count.** Confidence must reflect the quality and provenance of evidence, not the frequency of keyword matches.
2. **Calibrated thresholds.** Score thresholds and pillar weights must be derived from data, not chosen arbitrarily.
3. **Constrained LLM revision.** The LLM scorecard synthesizer must be bounded — it cannot produce a score more than ±15 points from the deterministic baseline per pillar.
4. **Transparent recommendations.** The recommendation must include confidence intervals and state what evidence drove it.
5. **Testable without live data.** All scoring logic must be exercisable from unit tests with synthetic evidence facts.

---

## 2. Signal Vocabulary Redesign

### 2.1 Expanded Keyword Vocabulary

Replace the current 4–7 keyword lists with expanded vocabularies (~15–25 keywords + synonym groups per signal):

```python
PILLAR_SIGNALS: dict[str, dict[str, SignalDefinition]] = {
    "Financial Engine": {
        "Profitability": SignalDefinition(
            primary_keywords=["roic", "return on invested capital", "margin", "ebitda",
                               "eps", "earnings per share", "net income", "operating income",
                               "gross profit", "operating leverage", "profit margin",
                               "return on equity", "roe", "roa", "profitability"],
            negative_keywords=["non-profit", "not-for-profit", "profit warning",
                                "profit-taking", "missed profit"],
            weight=1.0,
        ),
        ...
    }
}
```

**`SignalDefinition`** is a new dataclass:
```python
@dataclass
class SignalDefinition:
    primary_keywords: list[str]
    negative_keywords: list[str] = field(default_factory=list)  # subtract confidence
    weight: float = 1.0  # signal importance within pillar
```

### 2.2 Negation-Aware Matching

Before recording a signal hit, check if any negative keyword appears within a 30-word window around the match. A hit near a negation reduces confidence rather than adding it.

### 2.3 Semantic Expansion (Phase 2)

In Phase 2, replace keyword matching with embedding-based semantic similarity. Each signal gets a few "anchor sentences" (e.g., "the company has a strong competitive moat from network effects"). Evidence text similarity to anchor sentences is used alongside keywords for coverage scoring.

---

## 3. Pillar Scoring Redesign

### 3.1 Revised Formula

```
coverage_score = min(1.0, covered_signals / total_signals)
density_score  = min(1.0, evidence_count / 10)         # raise cap from 8 to 10
diversity_score = min(1.0, unique_sources / 5)          # raise cap from 4 to 5
quality_score  = weighted_avg_confidence               # weighted by signal_definition.weight

raw_score = (30 × coverage_score) + (20 × density_score) +
            (10 × diversity_score) + (40 × quality_score)
```

Changes from current:
- Coverage weight: 35 → 30 (less reward for breadth without quality).
- Quality weight: 35 → 40 (more reward for evidence quality).
- Density cap: 8 → 10.
- Diversity cap: 4 → 5.

### 3.2 Evidence Minimum Gate Revision

Current: `if evidence_count < 2: raw_score = min(raw_score, 50)`.

Revised: apply a graduated cap by evidence count:
```python
if evidence_count == 0: raw_score = 0
elif evidence_count == 1: raw_score = min(raw_score, 40)
elif evidence_count < 3: raw_score = min(raw_score, 55)
elif evidence_count < 5: raw_score = min(raw_score, 70)
# >= 5 evidence facts: no cap
```

This creates a smoother quality curve rather than a binary cliff at evidence_count=2.

### 3.3 Confidence Formula Fix

Replace the circular keyword-count confidence formula in `_extract_pillar_evidence_deterministic()`:

**Current (circular):**
```python
confidence = min(1.0, 0.45 + (0.1 * len(signal_hits)))
```

**Revised (evidence quality based):**
```python
def _evidence_confidence(*, source_title: str, body: str, has_metric: bool, is_primary: bool) -> float:
    base = 0.35
    if is_primary:   base += 0.20  # e.g., 10-K, annual report, earnings release
    if has_metric:   base += 0.15  # contains a concrete number
    if len(body) > 500: base += 0.05  # substantial text, not just a title mention
    return min(0.75, base)
```

`is_primary` = True when source URL contains `sec.gov`, `investor`, `annual-report`, `earnings`, or `10-k`.  
`has_metric` = True when the evidence excerpt contains a number followed by `%`, `$`, `bn`, `m`, or an explicit ratio.

---

## 4. Pillar Weights Calibration Plan

### 4.1 Initial Calibration (Phase 1)

Use the existing scoring system to score 50 well-known public companies. Have domain experts (or a reference dataset like analyst consensus) rate each as: Strong Buy / Buy / Hold / Sell / Strong Sell. Fit a logistic regression to predict analyst consensus from pillar scores. Use the regression coefficients as calibrated pillar weights.

### 4.2 Industry-Adjusted Weights (Phase 2)

Maintain separate weight tables for broad sectors:
```python
PILLAR_WEIGHTS_BY_SECTOR: dict[str, dict[str, float]] = {
    "Technology": {"Financial Engine": 0.20, "Economic Moat": 0.30, ...},
    "Consumer Staples": {"Financial Engine": 0.30, "Management": 0.20, ...},
    "Healthcare": {"Economic Moat": 0.30, "Macro & Industry": 0.25, ...},
    "default": PILLAR_WEIGHTS,  # existing weights as fallback
}
```

Sector is determined from the company ticker via a lookup (no live API call needed).

### 4.3 Partial Run Confidence Penalty

When fewer than 6 pillars are scored:
```python
FULL_PILLAR_COUNT = 6
partial_penalty = 1.0 - (0.05 * (FULL_PILLAR_COUNT - len(pillar_scores)))
overall_confidence = round(avg_pillar_confidence * partial_penalty, 3)
```

This ensures a 3-pillar run shows 85% of the confidence of a 6-pillar run at the same evidence quality.

---

## 5. Recommendation Logic Redesign

### 5.1 Confidence-Interval-Based Thresholds

Instead of hardcoded thresholds, use score + confidence to define a range:
```python
lower_bound = overall_score - (1 - overall_confidence) * 15
upper_bound = overall_score + (1 - overall_confidence) * 5
```

The recommendation is based on the lower bound (conservative). If lower_bound >= 75 → qualify for "Good Buy" tier. If lower_bound < 75 → at most "Watch".

### 5.2 Tighter "Good Buy" Requirements

Raise the evidence requirements for the highest recommendation tier:
```python
# Good Buy requires:
# - 5+ pillars with at least 3 evidence facts each
# - overall_score >= 78 (raised from 75)
# - financial_score >= 72 (raised from 70)
# - moat_score >= 68 (raised from 65)
# - overall_confidence >= 0.65 (added)
# - valuation_status != "Overvalued"
# - technical_state != "Bearish Trend"
```

### 5.3 Evidence-Derived Invalidation Conditions

Generate invalidation conditions from actual evidence gaps rather than boilerplate:
```python
def _build_invalidation_conditions(pillar_assessments: dict, pillar_scores: dict) -> list[str]:
    conditions = []
    for pillar, assessment in pillar_assessments.items():
        if assessment.get("gaps"):
            gap = assessment["gaps"][0]
            score = pillar_scores.get(pillar, 0)
            if score >= 60:
                conditions.append(
                    f"{pillar} ({gap}): if evidence on {gap.lower()} deteriorates in upcoming filings."
                )
    return conditions[:4] or ["No confirmed high-impact invalidation conditions identified."]
```

---

## 6. LLM Scorecard Synthesis Constraints

### 6.1 Bounded Score Revision

After the LLM returns a revised scorecard, apply hard bounds before returning:
```python
MAX_PILLAR_DELTA = 15  # LLM cannot move any pillar score by more than 15 points
MAX_OVERALL_DELTA = 12

def _apply_score_bounds(llm_scorecard: dict, deterministic_scorecard: dict) -> dict:
    for pillar in llm_scorecard.get("pillar_scores", {}):
        det_score = deterministic_scorecard.get("pillar_scores", {}).get(pillar, 50)
        llm_score = llm_scorecard["pillar_scores"][pillar]
        llm_scorecard["pillar_scores"][pillar] = max(
            det_score - MAX_PILLAR_DELTA,
            min(det_score + MAX_PILLAR_DELTA, llm_score)
        )
    det_overall = deterministic_scorecard.get("overall_score", 50)
    llm_scorecard["overall_score"] = max(
        det_overall - MAX_OVERALL_DELTA,
        min(det_overall + MAX_OVERALL_DELTA, llm_scorecard.get("overall_score", det_overall))
    )
    return llm_scorecard
```

### 6.2 Recommendation Jump Guard

The LLM recommendation must not jump more than one level from the deterministic baseline:
```python
RECOMMENDATION_LEVELS = [
    "Insufficient Data", "Avoid", "Hold / Not Attractive Now",
    "Watch / Accumulate on Weakness", "Wait for Dip", "Good Buy"
]

def _constrain_recommendation(llm_rec: str, det_rec: str) -> str:
    llm_idx = RECOMMENDATION_LEVELS.index(llm_rec) if llm_rec in RECOMMENDATION_LEVELS else -1
    det_idx = RECOMMENDATION_LEVELS.index(det_rec) if det_rec in RECOMMENDATION_LEVELS else -1
    if llm_idx == -1 or det_idx == -1:
        return det_rec
    if abs(llm_idx - det_idx) > 1:
        return det_rec  # reject LLM recommendation if it jumped more than one level
    return llm_rec
```

### 6.3 Full Evidence Exposure

Remove the 5-fact-per-pillar truncation in `_compact_evidence_for_scorecard()`. Send all facts (capped at 10 per pillar instead of 5), with excerpts at 350 characters instead of 280.

---

## 7. Testing Architecture

**Unit tests (no LLM, no DB, no network):**
- `assess_pillars()` with synthetic evidence facts → assert score formula
- `build_stock_scorecard()` with synthetic assessments → assert recommendation tree
- `_apply_score_bounds()` → assert LLM revision bounds
- `_constrain_recommendation()` → assert all jump scenarios

**Integration tests (mock LLM):**
- Full scoring pipeline from raw documents to scorecard
- LLM fallback path

**Regression tests:**
- Golden scorecard set: 10 well-known companies with expected recommendation range
- Run weekly; alert on recommendation drift
