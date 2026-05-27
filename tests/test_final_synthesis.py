"""Tests for LLM final memo synthesis."""

from __future__ import annotations

import json

import pytest

from research_core.schemas.final_synthesis import FinalResearchSynthesis
from research_core.synthesis.final_synthesis import (
    FinalSynthesisError,
    FinalSynthesisGenerator,
    build_personalized_recommendation,
    build_synthesis_payload,
    detect_market_status,
    sanitize_market_status_language,
)


def test_build_synthesis_payload_trims_evidence_and_sources():
    evidence = {
        "Valuation": [
            {
                "signal_name": "Multiples",
                "metric_name": "P/E",
                "metric_value": "10x",
                "period": "TTM",
                "excerpt": "x" * 500,
                "confidence": 0.8,
                "source_title": "R",
            }
        ]
    }
    sources = {
        "Valuation": [
            {"title": "A", "link": "https://a.com", "snippet": "y" * 400, "body": "", "source_kind": "ir", "is_primary_source": True},
        ]
    }
    payload = build_synthesis_payload(
        company_name="Acme",
        ticker="ACM",
        scorecard={"overall_score": 70, "recommendation": "Hold"},
        pillar_assessments={"Valuation": {"score": 70, "confidence": 0.6, "category": "Neutral", "synopsis": "s", "analysis": "a" * 2000, "strengths": [], "gaps": [], "evidence_count": 1}},
        evidence_by_pillar=evidence,
        sources_by_pillar=sources,
        pillars_order=["Valuation"],
    )
    assert len(payload["evidence_by_pillar"]["Valuation"][0]["excerpt"]) <= 400
    assert len(payload["sources_by_pillar"]["Valuation"][0]["snippet"]) <= 320
    assert len(payload["pillar_assessments"]["Valuation"]["analysis"]) <= 1200
    assert payload["user_context"]["profile_status"] == "anonymous_default"


def test_build_synthesis_payload_accepts_user_profiles():
    payload = build_synthesis_payload(
        company_name="Acme",
        ticker="ACM",
        scorecard={"overall_score": 70, "recommendation": "Hold"},
        pillar_assessments={},
        evidence_by_pillar={},
        sources_by_pillar={},
        pillars_order=[],
        user_financial_profile={"time_horizon": "10 years"},
        user_risk_profile={"risk_tolerance": "moderate"},
        investor_context={"primaryGoal": "wealth_building"},
    )
    assert payload["user_context"]["profile_status"] == "personalized"
    assert payload["user_context"]["financial_profile"]["time_horizon"] == "10 years"
    assert payload["user_context"]["risk_profile"]["risk_tolerance"] == "moderate"
    assert payload["user_context"]["investor_context"]["primaryGoal"] == "wealth_building"


def test_build_synthesis_payload_detects_pre_ipo_context():
    payload = build_synthesis_payload(
        company_name="Kasapreko PLC",
        ticker="KAS",
        scorecard={"overall_score": 70, "recommendation": "Watchlist"},
        pillar_assessments={},
        evidence_by_pillar={
            "Valuation": [
                {
                    "source_title": "Prospectus",
                    "excerpt": "The IPO offer price and proposed listing valuation are described in the prospectus.",
                }
            ]
        },
        sources_by_pillar={"Valuation": [{"title": "IPO Prospectus", "snippet": "initial public offering"}]},
        pillars_order=["Valuation"],
    )

    assert payload["market_status"]["status"] == "pre_ipo_or_not_trading"
    assert "prospectus" in payload["market_status"]["signals"]


def test_market_status_sanitizer_rewrites_pre_ipo_valuation_language():
    status = detect_market_status(
        scorecard={},
        evidence_by_pillar={"Valuation": [{"excerpt": "IPO prospectus with offer price."}]},
        sources_by_pillar={},
    )
    synthesis = {
        "pillarTakeaways": [
            {
                "pillarName": "Valuation",
                "plainEnglishSummary": "The stock is currently undervalued, presenting a potential buying opportunity.",
                "supportingPoints": ["Intrinsic value assessments indicate the stock is trading below its true worth."],
            }
        ]
    }

    cleaned = sanitize_market_status_language(synthesis, status)

    text = json.dumps(cleaned)
    assert "stock is currently undervalued" not in text
    assert "trading below its true worth" not in text
    assert "not yet trading" in text


def test_personalized_recommendation_neutral_without_profile():
    recommendation = build_personalized_recommendation(
        scorecard={
            "overall_score": 80,
            "recommendation": "Buy",
            "confidence": 0.8,
            "bullish_drivers": ["Strong cash generation."],
            "recommendation_confidence": "high",
        }
    )

    assert recommendation["investmentQuality"]["score"] == 80
    assert recommendation["investorFit"]["profileBasis"] == "anonymous_default"
    assert recommendation["investorFit"]["rating"] == "Neutral until profiled"
    assert recommendation["finalRecommendation"]["action"] == "Buy"


def test_personalized_recommendation_lowers_action_for_conservative_profile():
    recommendation = build_personalized_recommendation(
        scorecard={
            "overall_score": 82,
            "recommendation": "Buy",
            "confidence": 0.8,
            "bullish_drivers": ["Strong growth."],
            "recommendation_confidence": "high",
        },
        user_financial_profile={"financialResilience": "low"},
        user_risk_profile={"riskTolerance": "low", "riskCapacity": "low", "timeHorizon": "1_to_3"},
        investor_context={"restrictedSectors": ["crypto"]},
    )

    assert recommendation["investorFit"]["score"] < 45
    assert recommendation["finalRecommendation"]["action"] == "Watchlist"
    assert recommendation["investorFit"]["constraints"]


def test_personalized_recommendation_preserves_action_for_strong_fit():
    recommendation = build_personalized_recommendation(
        scorecard={
            "overall_score": 84,
            "recommendation": "Buy",
            "confidence": 0.82,
            "bullish_drivers": ["Durable margins."],
            "recommendation_confidence": "high",
        },
        user_financial_profile={"financialResilience": "high"},
        user_risk_profile={"riskTolerance": "high", "riskCapacity": "high", "timeHorizon": "10_plus"},
        investor_context={"restrictedSectors": []},
    )

    assert recommendation["investorFit"]["score"] >= 75
    assert recommendation["finalRecommendation"]["action"] == "Buy"


def test_final_synthesis_requires_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    gen = FinalSynthesisGenerator()
    with pytest.raises(FinalSynthesisError, match="OPENAI_API_KEY"):
        gen.generate(
            company_name="A",
            ticker="B",
            scorecard={},
            pillar_assessments={},
            evidence_by_pillar={},
            sources_by_pillar={},
            pillars_order=[],
        )


def test_final_synthesis_validates_llm_output(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class FakeStructured:
        def __init__(self, payload: dict):
            self._payload = payload

        def invoke(self, _messages):
            return self._payload

    class FakeChat:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(self, _schema):
            sample = {
                "companySnapshot": "Co does widgets.",
                "investmentTakeaway": "Hold.",
                "personalizedRecommendation": {
                    "investmentQuality": {
                        "score": 70,
                        "rating": "Mixed",
                        "rationale": ["Standalone quality is acceptable."],
                        "confidence": "Medium",
                    },
                    "investorFit": {
                        "score": 50,
                        "rating": "Neutral until profiled",
                        "rationale": ["No investor profile was available."],
                        "constraints": ["Risk tolerance is unknown."],
                        "profileBasis": "anonymous_default",
                    },
                    "finalRecommendation": {
                        "action": "Watchlist",
                        "confidence": "Medium",
                        "explanation": "Quality is mixed and fit is not personalized yet.",
                        "suitabilityNotes": ["This is a general research view."],
                    },
                },
                "recommendationRationale": ["Reason one", "Reason two"],
                "mainRisks": ["Risk one"],
                "pillarTakeaways": [
                    {
                        "pillarName": "Valuation",
                        "score": 70,
                        "plainEnglishSummary": "Summary.",
                        "position": "mixed",
                        "whyItMatters": "Matters.",
                        "supportingPoints": ["a"],
                        "watchItems": ["b"],
                        "technicalDetails": "P/E discussed.",
                    }
                ],
                "bottomLine": "Done.",
                "sourceNote": "Grounded.",
            }
            return FakeStructured(sample)

    monkeypatch.setattr("research_core.synthesis.final_synthesis.ChatOpenAI", FakeChat)
    gen = FinalSynthesisGenerator()
    out = gen.generate(
        company_name="Acme",
        ticker="ACM",
        scorecard={"overall_score": 70, "confidence": 0.6, "recommendation": "Hold"},
        pillar_assessments={"Valuation": {"score": 70, "confidence": 0.6, "category": "Neutral", "synopsis": "", "analysis": "", "strengths": [], "gaps": [], "evidence_count": 1}},
        evidence_by_pillar={},
        sources_by_pillar={},
        pillars_order=["Valuation"],
    )
    assert out["companySnapshot"] == "Co does widgets."
    assert out["personalizedRecommendation"]["investorFit"]["profileBasis"] == "anonymous_default"
    assert out["pillarTakeaways"][0]["pillarName"] == "Valuation"
    FinalResearchSynthesis.model_validate(json.loads(json.dumps(out)))


def test_final_synthesis_retries_then_raises(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("research_core.synthesis.final_synthesis.time.sleep", lambda _s: None)

    attempts = {"n": 0}

    class Boom:
        def invoke(self, _messages):
            attempts["n"] += 1
            raise ValueError("bad response")

    class FakeChat:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(self, _schema):
            return Boom()

    monkeypatch.setattr("research_core.synthesis.final_synthesis.ChatOpenAI", FakeChat)
    gen = FinalSynthesisGenerator()
    with pytest.raises(FinalSynthesisError, match="failed after"):
        gen.generate(
            company_name="A",
            ticker="B",
            scorecard={},
            pillar_assessments={},
            evidence_by_pillar={},
            sources_by_pillar={},
            pillars_order=[],
        )
    assert attempts["n"] == 3


def test_build_result_payload_includes_final_synthesis():
    from scope_api.application.run_service import _build_result_payload

    summary = {
        "ticker": "T",
        "stock_name": "Co",
        "query_batch_count": 1,
        "filtered_doc_count": 1,
        "scraped_doc_count": 1,
        "overall_score": 80,
        "recommendation": "Hold",
        "scorecard": {},
        "pillar_assessments": {},
        "evidence_by_pillar": {},
        "sources_by_pillar": {},
        "final_synthesis": {"companySnapshot": "x", "investmentTakeaway": "y"},
    }
    payload = _build_result_payload(summary)
    assert payload["finalSynthesis"]["companySnapshot"] == "x"
