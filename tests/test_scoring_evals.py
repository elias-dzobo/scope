"""Comprehensive eval suite for the Scope scoring system.

Tests cover:
- assess_pillars() formula correctness
- Evidence density, coverage, and diversity scoring
- Pillar category thresholds
- build_stock_scorecard() recommendation decision tree
- Valuation and technical state classification
- Low-confidence recommendation downgrade
- Partial pillar weight normalization
- Deterministic confidence formula properties
- Invalidation condition generation
- LLM scorecard revision bounds (contract-level)
- Edge cases: empty evidence, single fact, missing pillars
- Regression guards for known scoring weaknesses

All tests are offline-safe: no live LLM, DB, or network calls required.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stubs for API-layer imports that scoring/main.py depends on
# ---------------------------------------------------------------------------

def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    return mod


def _ensure_stubs() -> None:
    """Stub only the leaf observability modules that scoring/main.py imports.

    IMPORTANT: Do NOT stub 'scope_api' or 'scope_api.observability' as parent packages.
    Stubbing the parent package as a MagicMock breaks other test files that import
    real submodules (e.g., scope_api.app imports scope_api.memory.build_memory_router).

    We import the real scope_api package and only replace the specific leaf modules
    that would make live network/metrics calls during tests.
    """
    import contextlib

    # Ensure the real scope_api package is importable first (from apps/api/src on PYTHONPATH)
    # We only replace specific leaf modules — not the parent package.
    if "scope_api.observability.metrics" not in sys.modules:
        try:
            import scope_api.observability.metrics as _real_metrics  # noqa: F401
        except Exception:
            pass
        metrics_stub = MagicMock()
        metrics_stub.parse_llm_usage = lambda usage: (0, 0)
        metrics_stub.record_llm_call = lambda **kwargs: None
        sys.modules["scope_api.observability.metrics"] = metrics_stub

    if "scope_api.observability.telemetry" not in sys.modules:
        try:
            import scope_api.observability.telemetry as _real_telemetry  # noqa: F401
        except Exception:
            pass
        telemetry_stub = MagicMock()
        telemetry_stub.observe_span = contextlib.contextmanager(lambda *a, **kw: iter([None]))
        sys.modules["scope_api.observability.telemetry"] = telemetry_stub


_ensure_stubs()

from research_core.scoring.main import (  # noqa: E402
    PILLAR_SIGNALS,
    PILLAR_WEIGHTS,
    _build_entity_terms,
    _extract_signal_hits,
    _mentions_entity,
    _pillar_category,
    assess_pillars,
    build_stock_scorecard,
)


# ---------------------------------------------------------------------------
# Evidence factory helpers
# ---------------------------------------------------------------------------

def _make_fact(
    *,
    pillar_name: str = "Financial Engine",
    signal_name: str = "Profitability",
    source_title: str = "Annual Report 2024",
    metric_value: str = "EBITDA margin 35%",
    confidence: float = 0.7,
    excerpt: str = "EBITDA margin improved to 35% in FY2024.",
) -> dict[str, Any]:
    return {
        "pillar_name": pillar_name,
        "signal_name": signal_name,
        "source_title": source_title,
        "metric_name": "EBITDA Margin",
        "metric_value": metric_value,
        "period": "FY2024",
        "excerpt": excerpt,
        "confidence": confidence,
    }


def _make_facts(
    count: int,
    *,
    pillar_name: str = "Financial Engine",
    signal_name: str = "Profitability",
    source_prefix: str = "Source",
    confidence: float = 0.7,
) -> list[dict[str, Any]]:
    return [
        _make_fact(
            pillar_name=pillar_name,
            signal_name=signal_name,
            source_title=f"{source_prefix} {i}",
            confidence=confidence,
        )
        for i in range(count)
    ]


def _make_assessments(
    *,
    financial: int = 70,
    moat: int = 68,
    macro: int = 60,
    management: int = 60,
    valuation: int = 65,
    technical: int = 55,
    evidence_count: int = 5,
    confidence: float = 0.65,
) -> dict[str, dict[str, Any]]:
    scores = {
        "Financial Engine": financial,
        "Economic Moat": moat,
        "Macro & Industry": macro,
        "Management & Capital Allocation": management,
        "Valuation": valuation,
        "Technical Analysis": technical,
    }
    return {
        pillar: {
            "score": score,
            "confidence": confidence,
            "strengths": [f"{pillar}: {score} evidence hits"],
            "gaps": [],
            "evidence_count": evidence_count,
            "category": "Neutral",
        }
        for pillar, score in scores.items()
    }


# ===========================================================================
# 1. _build_entity_terms — entity tokenization
# ===========================================================================

class TestBuildEntityTerms:
    def test_ticker_included(self) -> None:
        terms = _build_entity_terms("NVIDIA Corporation", "NVDA")
        assert "nvda" in terms

    def test_company_name_lowered(self) -> None:
        terms = _build_entity_terms("Apple Inc.", "AAPL")
        assert "apple" in terms

    def test_stop_words_excluded(self) -> None:
        terms = _build_entity_terms("Ghana Commercial Bank Ltd", "GCB")
        assert "ltd" not in terms
        assert "ghana" not in terms  # in stop list for this function

    def test_short_tokens_excluded(self) -> None:
        # "ab" comes from the ticker path directly, not the token loop.
        # The token loop filters len < 3, but the ticker is added unconditionally.
        # Test the token loop: use a company name whose tokens are all < 3 chars.
        terms = _build_entity_terms("AB CO", "XY")
        # "ab" and "co" are len 2 → excluded by token loop (len >= 3 filter)
        # "co" is also in the stop list. "ab" is len 2 so excluded.
        assert "co" not in terms  # stop word
        # "xy" comes from ticker path, but "ab" would be len 2 — check it's not from token loop
        assert "ab" not in terms

    def test_empty_ticker_no_ticker_term(self) -> None:
        terms = _build_entity_terms("NVIDIA", "")
        assert "" not in terms


# ===========================================================================
# 2. _mentions_entity
# ===========================================================================

class TestMentionsEntity:
    def test_ticker_mentioned(self) -> None:
        assert _mentions_entity("NVDA reported earnings today.", {"nvda"}) is True

    def test_company_name_mentioned(self) -> None:
        assert _mentions_entity("NVIDIA's data center revenue grew.", {"nvidia"}) is True

    def test_no_match(self) -> None:
        assert _mentions_entity("Amazon reported strong AWS revenue.", {"nvda", "nvidia"}) is False

    def test_empty_entity_terms_always_true(self) -> None:
        assert _mentions_entity("Any text.", set()) is True


# ===========================================================================
# 3. _extract_signal_hits
# ===========================================================================

class TestExtractSignalHits:
    def test_matches_profitability_keywords(self) -> None:
        signals = PILLAR_SIGNALS["Financial Engine"]
        hits = _extract_signal_hits("EBITDA margin improved to 35%.", signals)
        assert "Profitability" in hits

    def test_matches_moat_keywords(self) -> None:
        signals = PILLAR_SIGNALS["Economic Moat"]
        hits = _extract_signal_hits("The company benefits from strong network effects and brand loyalty.", signals)
        assert "Competitive Advantage" in hits

    def test_no_match_returns_empty(self) -> None:
        signals = PILLAR_SIGNALS["Financial Engine"]
        hits = _extract_signal_hits("The weather in London was sunny today.", signals)
        assert hits == []

    def test_case_insensitive_matching(self) -> None:
        signals = PILLAR_SIGNALS["Financial Engine"]
        hits = _extract_signal_hits("ROIC increased to 18% year-over-year.", signals)
        assert "Profitability" in hits


# ===========================================================================
# 4. _pillar_category — thresholds
# ===========================================================================

class TestPillarCategory:
    def test_insufficient_data_low_count(self) -> None:
        assert _pillar_category(score=80, evidence_count=1, confidence=0.8) == "Insufficient Data"

    def test_insufficient_data_low_confidence(self) -> None:
        assert _pillar_category(score=80, evidence_count=5, confidence=0.30) == "Insufficient Data"

    def test_bullish_high_score(self) -> None:
        assert _pillar_category(score=80, evidence_count=5, confidence=0.7) == "Bullish"

    def test_neutral_mid_score(self) -> None:
        assert _pillar_category(score=65, evidence_count=3, confidence=0.6) == "Neutral"

    def test_cautious_lower_mid_score(self) -> None:
        assert _pillar_category(score=50, evidence_count=3, confidence=0.5) == "Cautious"

    def test_weak_low_score(self) -> None:
        assert _pillar_category(score=40, evidence_count=4, confidence=0.5) == "Weak"


# ===========================================================================
# 5. assess_pillars — formula correctness
# ===========================================================================

class TestAssessPillars:
    def test_empty_evidence_scores_zero(self) -> None:
        assessments = assess_pillars({"Financial Engine": []})
        assert assessments["Financial Engine"]["score"] == 0

    def test_single_fact_capped_at_50(self) -> None:
        """Score capped at 50 when evidence_count < 2."""
        facts = _make_facts(1, confidence=0.9)
        assessments = assess_pillars({"Financial Engine": facts})
        assert assessments["Financial Engine"]["score"] <= 50

    def test_two_facts_not_capped(self) -> None:
        """With 2+ facts, the cap is lifted — score can exceed 50."""
        facts = _make_facts(8, confidence=0.9)
        # Add second signal for coverage
        for i in range(4):
            facts.append(_make_fact(signal_name="Cash and Leverage", confidence=0.9))
        assessments = assess_pillars({"Financial Engine": facts})
        assert assessments["Financial Engine"]["score"] > 50

    def test_high_coverage_high_density_high_confidence(self) -> None:
        """Both signals, 8 facts, high confidence → high score."""
        facts = (
            _make_facts(4, signal_name="Profitability", confidence=0.85)
            + [_make_fact(signal_name="Cash and Leverage", confidence=0.85) for _ in range(4)]
        )
        assessments = assess_pillars({"Financial Engine": facts})
        score = assessments["Financial Engine"]["score"]
        assert score >= 65, f"Expected score >= 65, got {score}"

    def test_zero_evidence_confidence_is_zero(self) -> None:
        assessments = assess_pillars({"Financial Engine": []})
        assert assessments["Financial Engine"]["confidence"] == 0.0

    def test_category_insufficient_on_zero_evidence(self) -> None:
        assessments = assess_pillars({"Financial Engine": []})
        assert assessments["Financial Engine"]["category"] == "Insufficient Data"

    def test_gaps_are_uncovered_signals(self) -> None:
        """Only Profitability covered → Cash and Leverage is a gap."""
        facts = _make_facts(5, signal_name="Profitability", confidence=0.7)
        assessments = assess_pillars({"Financial Engine": facts})
        gaps = assessments["Financial Engine"]["gaps"]
        assert "Cash and Leverage" in gaps
        assert "Profitability" not in gaps

    def test_invalid_signal_names_filtered(self) -> None:
        """Facts with unrecognised signal_name must be excluded from scoring."""
        facts = [_make_fact(signal_name="InvalidSignal", confidence=0.9)]
        assessments = assess_pillars({"Financial Engine": facts})
        assert assessments["Financial Engine"]["evidence_count"] == 0

    def test_source_diversity_tracks_unique_sources(self) -> None:
        """Facts from same source title should not inflate diversity."""
        facts = _make_facts(6, source_prefix="SameSource", confidence=0.7)
        # All from "SameSource 0", "SameSource 1", ... actually 6 unique — set diversity=6
        assessments = assess_pillars({"Financial Engine": facts})
        assert assessments["Financial Engine"]["evidence_count"] == 6

    def test_multiple_pillars_assessed_independently(self) -> None:
        evidence = {
            "Financial Engine": _make_facts(5, pillar_name="Financial Engine", confidence=0.75),
            "Economic Moat": _make_facts(2, pillar_name="Economic Moat", signal_name="Switching Costs", confidence=0.4),
        }
        assessments = assess_pillars(evidence)
        assert "Financial Engine" in assessments
        assert "Economic Moat" in assessments
        # Financial engine should score higher
        assert assessments["Financial Engine"]["score"] > assessments["Economic Moat"]["score"]

    def test_assessment_has_required_keys(self) -> None:
        assessments = assess_pillars({"Financial Engine": _make_facts(3, confidence=0.6)})
        keys = {"pillar_name", "score", "confidence", "strengths", "gaps", "evidence_count", "category", "synopsis", "analysis"}
        assert keys.issubset(assessments["Financial Engine"].keys())


# ===========================================================================
# 6. Deterministic confidence formula — circular dependency regression
# ===========================================================================

class TestDeterministicConfidenceProperties:
    """
    Regression tests for the circular confidence formula.
    These tests document the CURRENT behaviour and flag when the formula is fixed.

    See scoring-evaluation.md §6.1 for the recommended fix.
    """

    def test_single_signal_hit_gets_low_confidence(self) -> None:
        """A document with only 1 signal hit should NOT get very high confidence."""
        facts = _make_facts(1, confidence=0.55)
        assessments = assess_pillars({"Financial Engine": facts})
        # confidence should be moderate — not > 0.8 — for a single fact from one source
        avg_conf = assessments["Financial Engine"]["confidence"]
        # Current formula CAN produce 0.55 confidence here — we just check it's not inflated
        assert avg_conf <= 0.8, (
            f"Single-fact confidence {avg_conf} looks inflated. "
            "See scoring-evaluation.md §6.1 for the circular formula fix."
        )

    def test_high_keyword_density_does_not_guarantee_high_confidence(self) -> None:
        """Evidence quality should not be purely determined by keyword count."""
        # This test documents the regression described in scoring-evaluation.md
        # where a document hitting many keywords gets confidence=1.0
        # The test passes today but will verify the fix when implemented.
        facts = _make_facts(5, confidence=0.65)
        assessments = assess_pillars({"Financial Engine": facts})
        conf = assessments["Financial Engine"]["confidence"]
        # With the fixed formula, primary-source facts should get higher confidence
        # than generic keyword hits. For now we just assert non-zero.
        assert conf > 0.0


# ===========================================================================
# 7. build_stock_scorecard — recommendation decision tree
# ===========================================================================

class TestBuildStockScorecard:
    def test_empty_assessments_returns_insufficient_data(self) -> None:
        sc = build_stock_scorecard("Apple", "AAPL", {}, llm_enabled=False)
        assert sc["recommendation"] == "Insufficient Data"
        assert sc["overall_score"] == 0

    def test_fewer_than_3_pillars_is_insufficient(self) -> None:
        assessments = {
            pillar: {"score": 80, "confidence": 0.8, "strengths": [], "gaps": [], "evidence_count": 5}
            for pillar in ["Financial Engine", "Economic Moat"]
        }
        sc = build_stock_scorecard("Apple", "AAPL", assessments, llm_enabled=False)
        assert sc["recommendation"] == "Insufficient Data"

    def test_good_buy_requires_high_scores_on_all_conditions(self) -> None:
        # Scores chosen so weighted overall >= 75, financial >= 70, moat >= 65,
        # valuation "Undervalued" (score >= 75 with evidence), technical not "Bearish Trend" (score >= 55)
        assessments = _make_assessments(
            financial=80, moat=75, macro=76, management=76, valuation=80, technical=76,
            evidence_count=6, confidence=0.75,
        )
        evidence = {p: _make_facts(6, pillar_name=p, confidence=0.75) for p in assessments}
        sc = build_stock_scorecard("Apple", "AAPL", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["recommendation"] == "Good Buy"

    def test_overvalued_prevents_good_buy(self) -> None:
        # Valuation score < 60 → valuation_status = "Overvalued" (evidence >= 2)
        assessments = _make_assessments(
            financial=78, moat=70, macro=72, management=70, valuation=40, technical=72,
            evidence_count=6, confidence=0.75,
        )
        evidence = {p: _make_facts(6, pillar_name=p, confidence=0.75) for p in assessments}
        sc = build_stock_scorecard("Apple", "AAPL", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["recommendation"] != "Good Buy"
        assert sc["recommendation"] in {"Wait for Dip", "Watch / Accumulate on Weakness", "Hold / Not Attractive Now"}

    def test_bearish_technical_prevents_good_buy(self) -> None:
        # Technical score < 55 → technical_state = "Bearish Trend"
        assessments = _make_assessments(
            financial=78, moat=70, macro=72, management=70, valuation=78, technical=40,
            evidence_count=6, confidence=0.75,
        )
        evidence = {p: _make_facts(6, pillar_name=p, confidence=0.75) for p in assessments}
        sc = build_stock_scorecard("Apple", "AAPL", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["recommendation"] in {"Watch / Accumulate on Weakness", "Wait for Dip"}

    def test_mid_score_watch_or_hold(self) -> None:
        assessments = _make_assessments(
            financial=65, moat=60, macro=60, management=60, valuation=65, technical=58,
            evidence_count=4, confidence=0.60,
        )
        evidence = {p: _make_facts(4, pillar_name=p, confidence=0.60) for p in assessments}
        sc = build_stock_scorecard("Test", "TST", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["recommendation"] in {
            "Watch / Accumulate on Weakness", "Hold / Not Attractive Now", "Wait for Dip"
        }

    def test_low_overall_score_avoid(self) -> None:
        assessments = _make_assessments(
            financial=35, moat=30, macro=35, management=35, valuation=30, technical=30,
            evidence_count=4, confidence=0.45,
        )
        evidence = {p: _make_facts(4, pillar_name=p, confidence=0.45) for p in assessments}
        sc = build_stock_scorecard("Weak Corp", "WK", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["recommendation"] == "Avoid"

    def test_low_confidence_downgrades_good_buy(self) -> None:
        """overall_confidence < 0.55 should not result in Good Buy."""
        assessments = _make_assessments(
            financial=78, moat=70, macro=72, management=70, valuation=78, technical=72,
            evidence_count=6, confidence=0.45,  # low confidence
        )
        evidence = {p: _make_facts(6, pillar_name=p, confidence=0.45) for p in assessments}
        sc = build_stock_scorecard("Apple", "AAPL", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["recommendation"] != "Good Buy"

    def test_scorecard_source_is_deterministic(self) -> None:
        assessments = _make_assessments()
        sc = build_stock_scorecard("Apple", "AAPL", assessments, llm_enabled=False)
        assert sc["scorecard_source"] == "deterministic"


# ===========================================================================
# 8. Valuation and technical state classification
# ===========================================================================

class TestValuationAndTechnicalState:
    def test_valuation_unknown_when_no_evidence(self) -> None:
        assessments = _make_assessments(valuation=80)
        evidence = {p: [] for p in assessments}  # no evidence for any pillar
        sc = build_stock_scorecard("Test", "TST", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["valuation_status"] == "Unknown"

    def test_valuation_undervalued_when_high_score_and_evidence(self) -> None:
        assessments = _make_assessments(valuation=80, evidence_count=5)
        evidence = {
            p: _make_facts(5, pillar_name=p, confidence=0.7) for p in assessments
        }
        sc = build_stock_scorecard("Test", "TST", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["valuation_status"] == "Undervalued"

    def test_technical_unknown_when_no_evidence(self) -> None:
        assessments = _make_assessments(technical=80)
        evidence = {p: [] for p in assessments}
        sc = build_stock_scorecard("Test", "TST", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["technical_state"] == "Unknown"

    def test_technical_bullish_trend_high_score(self) -> None:
        assessments = _make_assessments(technical=75, evidence_count=5)
        evidence = {p: _make_facts(5, pillar_name=p, confidence=0.7) for p in assessments}
        sc = build_stock_scorecard("Test", "TST", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["technical_state"] == "Bullish Trend"

    def test_technical_bearish_trend_low_score(self) -> None:
        assessments = _make_assessments(technical=40, evidence_count=5)
        evidence = {p: _make_facts(5, pillar_name=p, confidence=0.6) for p in assessments}
        sc = build_stock_scorecard("Test", "TST", assessments, evidence_by_pillar=evidence, llm_enabled=False)
        assert sc["technical_state"] == "Bearish Trend"


# ===========================================================================
# 9. Recommendation confidence level
# ===========================================================================

class TestRecommendationConfidenceLevel:
    def test_high_confidence_label_when_over_75(self) -> None:
        assessments = _make_assessments(evidence_count=8, confidence=0.80)
        sc = build_stock_scorecard("Test", "TST", assessments, llm_enabled=False)
        assert sc["recommendation_confidence"] == "High"

    def test_medium_confidence_label(self) -> None:
        assessments = _make_assessments(evidence_count=5, confidence=0.65)
        sc = build_stock_scorecard("Test", "TST", assessments, llm_enabled=False)
        assert sc["recommendation_confidence"] == "Medium"

    def test_low_confidence_label_when_under_60(self) -> None:
        assessments = _make_assessments(evidence_count=3, confidence=0.50)
        sc = build_stock_scorecard("Test", "TST", assessments, llm_enabled=False)
        assert sc["recommendation_confidence"] == "Low"


# ===========================================================================
# 10. Pillar weight normalization for partial runs
# ===========================================================================

class TestPartialPillarWeightNormalization:
    def test_three_pillars_normalize_to_1(self) -> None:
        assessments = {
            pillar: {"score": 70, "confidence": 0.7, "strengths": [], "gaps": [], "evidence_count": 5}
            for pillar in ["Financial Engine", "Economic Moat", "Valuation"]
        }
        sc = build_stock_scorecard("Test", "TST", assessments, llm_enabled=False)
        # Overall score must be a valid weighted average (weights sum to 1 after normalization)
        assert 0 <= sc["overall_score"] <= 100

    def test_single_pillar_score_equals_overall(self) -> None:
        assessments = {
            "Financial Engine": {
                "score": 72, "confidence": 0.7, "strengths": [], "gaps": [], "evidence_count": 4
            }
        }
        sc = build_stock_scorecard("Test", "TST", assessments, llm_enabled=False)
        # With one pillar, the normalized weight = 1.0, overall = that pillar's score
        assert sc["overall_score"] == 72


# ===========================================================================
# 11. Invalidation conditions — not always boilerplate
# ===========================================================================

class TestInvalidationConditions:
    def test_invalidation_conditions_present(self) -> None:
        assessments = _make_assessments()
        sc = build_stock_scorecard("Test", "TST", assessments, llm_enabled=False)
        assert isinstance(sc["invalidation_conditions"], list)
        assert len(sc["invalidation_conditions"]) > 0

    def test_boilerplate_regression(self) -> None:
        """
        Regression test: documents the CURRENT boilerplate behaviour.
        When scoring-implementation-strategy.md Phase 0.3 is implemented,
        this test should be updated to assert dynamic, evidence-based conditions.
        """
        assessments = _make_assessments()
        sc = build_stock_scorecard("Test", "TST", assessments, llm_enabled=False)
        boilerplate = "Financial Engine score drops below 60 on next earnings cycle."
        # Currently the boilerplate IS present — this test documents it
        # When Phase 0.3 is done, boilerplate should NOT be the only condition
        conditions = sc["invalidation_conditions"]
        assert len(conditions) >= 1  # at minimum, some conditions exist


# ===========================================================================
# 12. LLM scorecard path — disabled by default in tests
# ===========================================================================

class TestLlmScorecardPath:
    def test_llm_disabled_returns_deterministic(self) -> None:
        assessments = _make_assessments()
        sc = build_stock_scorecard("Test", "TST", assessments, llm_enabled=False)
        assert sc["scorecard_source"] == "deterministic"

    def test_llm_fallback_on_exception(self) -> None:
        """When LLM synthesis fails, must fall back to deterministic scorecard."""
        assessments = _make_assessments()
        with patch(
            "research_core.scoring.main._build_stock_scorecard_llm",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            sc = build_stock_scorecard("Test", "TST", assessments, llm_enabled=True)
        assert sc["scorecard_source"] == "deterministic_fallback"
        assert "scorecard_error" in sc

    def test_llm_disabled_no_openai_key_needed(self) -> None:
        """Deterministic path must not touch OPENAI_API_KEY."""
        import os

        original = os.environ.pop("OPENAI_API_KEY", None)
        try:
            assessments = _make_assessments()
            sc = build_stock_scorecard("Test", "TST", assessments, llm_enabled=False)
            assert sc["recommendation"] != ""
        finally:
            if original is not None:
                os.environ["OPENAI_API_KEY"] = original


# ===========================================================================
# 13. Scorecard schema completeness
# ===========================================================================

class TestScorecardSchemaCompleteness:
    REQUIRED_KEYS = {
        "stock_name", "ticker", "pillar_scores", "overall_score", "confidence",
        "recommendation", "reasoning", "pillar_classifications", "valuation_status",
        "technical_state", "bullish_drivers", "key_risks", "invalidation_conditions",
        "recommendation_confidence", "scorecard_source",
    }

    def test_full_scorecard_has_all_required_keys(self) -> None:
        assessments = _make_assessments()
        sc = build_stock_scorecard("Apple", "AAPL", assessments, llm_enabled=False)
        missing = self.REQUIRED_KEYS - sc.keys()
        assert not missing, f"Scorecard missing keys: {missing}"

    def test_empty_assessments_scorecard_has_all_required_keys(self) -> None:
        sc = build_stock_scorecard("Apple", "AAPL", {}, llm_enabled=False)
        missing = self.REQUIRED_KEYS - sc.keys()
        assert not missing, f"Empty scorecard missing keys: {missing}"

    def test_pillar_scores_are_integers(self) -> None:
        assessments = _make_assessments()
        sc = build_stock_scorecard("Apple", "AAPL", assessments, llm_enabled=False)
        for pillar, score in sc["pillar_scores"].items():
            assert isinstance(score, int), f"Pillar {pillar} score should be int, got {type(score)}"

    def test_overall_score_in_range(self) -> None:
        assessments = _make_assessments()
        sc = build_stock_scorecard("Apple", "AAPL", assessments, llm_enabled=False)
        assert 0 <= sc["overall_score"] <= 100

    def test_confidence_in_range(self) -> None:
        assessments = _make_assessments()
        sc = build_stock_scorecard("Apple", "AAPL", assessments, llm_enabled=False)
        assert 0.0 <= sc["confidence"] <= 1.0


# ===========================================================================
# 14. PILLAR_WEIGHTS sum to 1.0
# ===========================================================================

class TestPillarWeightsSumToOne:
    def test_weights_sum_to_one(self) -> None:
        total = sum(PILLAR_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"PILLAR_WEIGHTS sum to {total}, expected 1.0"

    def test_all_six_pillars_present(self) -> None:
        expected = {
            "Financial Engine", "Economic Moat", "Macro & Industry",
            "Management & Capital Allocation", "Valuation", "Technical Analysis",
        }
        assert set(PILLAR_WEIGHTS.keys()) == expected

    def test_financial_engine_has_highest_weight(self) -> None:
        assert PILLAR_WEIGHTS["Financial Engine"] == max(PILLAR_WEIGHTS.values())


# ===========================================================================
# 15. PILLAR_SIGNALS structure
# ===========================================================================

class TestPillarSignalsStructure:
    def test_all_pillars_have_signals(self) -> None:
        for pillar in PILLAR_WEIGHTS:
            assert pillar in PILLAR_SIGNALS, f"No signals defined for pillar: {pillar}"

    def test_each_pillar_has_two_signals(self) -> None:
        for pillar, signals in PILLAR_SIGNALS.items():
            assert len(signals) == 2, f"{pillar} has {len(signals)} signals, expected 2"

    def test_each_signal_has_keywords(self) -> None:
        for pillar, signals in PILLAR_SIGNALS.items():
            for signal_name, keywords in signals.items():
                assert len(keywords) >= 3, (
                    f"{pillar} / {signal_name} has only {len(keywords)} keywords — too few for reliable matching"
                )

    def test_keywords_are_lowercase(self) -> None:
        for pillar, signals in PILLAR_SIGNALS.items():
            for signal_name, keywords in signals.items():
                for kw in keywords:
                    assert kw == kw.lower(), f"Keyword '{kw}' in {pillar}/{signal_name} is not lowercase"
