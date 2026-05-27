from research_core.scoring.main import build_stock_scorecard


def _assessment(score: int, confidence: float = 0.8) -> dict:
    return {
        "pillar_name": "",
        "score": score,
        "confidence": confidence,
        "strengths": ["signal"],
        "gaps": ["gap"],
        "evidence_count": 4,
    }


def test_recommendation_wait_for_dip_when_quality_high_but_overvalued():
    pillar_assessments = {
        "Macro & Industry": _assessment(80),
        "Economic Moat": _assessment(82),
        "Financial Engine": _assessment(85),
        "Management & Capital Allocation": _assessment(78),
        "Valuation": _assessment(45),
        "Technical Analysis": _assessment(72),
    }

    scorecard = build_stock_scorecard("Acme", "ACME", pillar_assessments)
    assert scorecard["recommendation"] == "Wait for Dip"
    assert scorecard["valuation_status"] == "Overvalued"


def test_recommendation_avoid_on_weak_profile():
    pillar_assessments = {
        "Macro & Industry": _assessment(45, 0.5),
        "Economic Moat": _assessment(48, 0.5),
        "Financial Engine": _assessment(40, 0.45),
        "Management & Capital Allocation": _assessment(50, 0.5),
        "Valuation": _assessment(52, 0.5),
        "Technical Analysis": _assessment(44, 0.5),
    }

    scorecard = build_stock_scorecard("Acme", "ACME", pillar_assessments)
    assert scorecard["recommendation"] == "Avoid"
    assert scorecard["pillar_classifications"]["Financial Engine"] == "Weak"
