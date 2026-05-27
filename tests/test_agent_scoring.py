from research_core.scoring.main import extract_evidence_facts, assess_pillars, build_stock_scorecard


def test_extract_evidence_facts_detects_signal_hits():
    scraped = {
        "Valuation": [
            {
                "title": "Acme valuation update",
                "body": "Analysts discuss P/E and EV/EBITDA multiples. A DCF fair value implies 15% discount.",
            }
        ]
    }

    evidence = extract_evidence_facts(scraped, llm_enabled=False)

    assert "Valuation" in evidence
    assert len(evidence["Valuation"]) >= 2
    signals = {fact["signal_name"] for fact in evidence["Valuation"]}
    assert "Multiples" in signals
    assert "Intrinsic Value" in signals


def test_assess_and_scorecard_generate_recommendation():
    evidence = {
        "Valuation": [
            {
                "pillar_name": "Valuation",
                "signal_name": "Multiples",
                "source_title": "A",
                "excerpt": "e",
                "confidence": 0.8,
            },
            {
                "pillar_name": "Valuation",
                "signal_name": "Intrinsic Value",
                "source_title": "B",
                "excerpt": "e",
                "confidence": 0.8,
            },
        ]
    }

    assessments = assess_pillars(evidence)
    scorecard = build_stock_scorecard("Acme Corp", "ACME", assessments)

    assert assessments["Valuation"]["score"] > 0
    assert assessments["Valuation"]["analysis"]
    assert scorecard["overall_score"] > 0
    assert scorecard["recommendation"] in {
        "Good Buy",
        "Watch / Accumulate on Weakness",
        "Wait for Dip",
        "Hold / Not Attractive Now",
        "Avoid",
        "Insufficient Data",
    }


def test_assess_pillars_ignores_unknown_signal_names():
    evidence = {
        "Macro & Industry": [
            {
                "pillar_name": "Macro & Industry",
                "signal_name": "Random Non-Configured Signal",
                "source_title": "X",
                "excerpt": "x",
                "confidence": 0.9,
            }
        ]
    }

    assessments = assess_pillars(evidence)
    assert assessments["Macro & Industry"]["score"] <= 50
    assert assessments["Macro & Industry"]["category"] == "Insufficient Data"
    assert assessments["Macro & Industry"]["analysis"]


def test_build_stock_scorecard_can_use_llm_assisted_synthesis(monkeypatch):
    class FakeStructured:
        def invoke(self, _messages):
            return {
                "stock_name": "Acme Corp",
                "ticker": "ACME",
                "pillar_scores": {"Valuation": 71},
                "overall_score": 71,
                "confidence": 0.7,
                "recommendation": "Watch / Accumulate on Weakness",
                "reasoning": "LLM synthesized scorecard from evidence and baseline.",
                "pillar_classifications": {"Valuation": "Neutral"},
                "valuation_status": "Fairly Valued",
                "technical_state": "Unknown",
                "bullish_drivers": ["Valuation: fair value evidence"],
                "key_risks": ["Evidence remains limited"],
                "invalidation_conditions": ["Valuation evidence deteriorates."],
                "recommendation_confidence": "Medium",
            }

    class FakeChat:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(self, _schema):
            return FakeStructured()

    monkeypatch.setattr("research_core.scoring.main.ChatOpenAI", FakeChat)
    assessments = {
        "Valuation": {
            "pillar_name": "Valuation",
            "score": 65,
            "confidence": 0.65,
            "strengths": ["Multiples: 2 evidence hits"],
            "gaps": [],
            "evidence_count": 3,
        }
    }

    scorecard = build_stock_scorecard("Acme Corp", "ACME", assessments, llm_enabled=True)

    assert scorecard["scorecard_source"] == "llm_assisted"
    assert scorecard["overall_score"] == 71
    assert scorecard["deterministic_baseline"]["scorecard_source"] == "deterministic"


def test_build_stock_scorecard_falls_back_when_llm_fails(monkeypatch):
    class FailingChat:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(self, _schema):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr("research_core.scoring.main.ChatOpenAI", FailingChat)
    assessments = {
        "Valuation": {
            "pillar_name": "Valuation",
            "score": 65,
            "confidence": 0.65,
            "strengths": ["Multiples: 2 evidence hits"],
            "gaps": [],
            "evidence_count": 3,
        }
    }

    scorecard = build_stock_scorecard("Acme Corp", "ACME", assessments, llm_enabled=True)

    assert scorecard["scorecard_source"] == "deterministic_fallback"
    assert "model unavailable" in scorecard["scorecard_error"]
