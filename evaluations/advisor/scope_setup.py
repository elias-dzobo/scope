"""Scope-specific fixtures for advisor agent evals.

DeepEval can run metrics and reports, but it should not know how to create
Scope users, profile snapshots, memory graph records, or fake research tools.
Those product-specific concerns live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scope_api import db


MODERATE_PROFILE = {
    "financialProfile": {
        "financialResilience": "moderate",
        "emergencyFundStrength": "healthy",
        "debtBurden": "low",
    },
    "riskProfile": {
        "riskTolerance": "moderate",
        "riskCapacity": "moderate",
    },
    "investorContext": {
        "primaryGoal": "wealth_building",
        "timeHorizon": "5_to_10",
    },
    "profileNarrative": {
        "headline": "Moderate investor with balanced growth goals.",
    },
}


@dataclass
class EvalEnvironment:
    """Runtime objects for one isolated eval case."""

    user: dict[str, Any]
    tmpdir: TemporaryDirectory[str]

    def cleanup(self) -> None:
        """Remove temporary runtime state for the case."""
        self.tmpdir.cleanup()


class FakeCompanyResearchController:
    """Deterministic company research tool for advisor evals."""

    def run_company_research(self, **kwargs) -> dict[str, Any]:
        """Return a stable company research summary."""
        ticker = kwargs["ticker"]
        company = kwargs["company_name"]
        return {
            "ticker": ticker,
            "stock_name": company,
            "scorecard": {"overall_score": 78, "recommendation": "Watchlist"},
            "final_synthesis": {
                "companySnapshot": f"{company} has exposure to AI infrastructure demand.",
                "investmentTakeaway": f"AI infrastructure spending is a meaningful demand driver for {ticker}.",
                "mainRisks": ["Valuation could already reflect optimistic growth expectations."],
                "sourceNote": "Deterministic eval fixture.",
            },
        }


class FakeGenericResearchHarness:
    """Deterministic generic research tool for advisor evals."""

    def run(self, **kwargs) -> dict[str, Any]:
        """Return a stable thematic research result."""
        return {
            "status": "completed",
            "query": kwargs["query"],
            "mode": "generic_financial_research",
            "themes": ["ai infrastructure", "semiconductors"],
            "synthesis": (
                "AI infrastructure spending can increase semiconductor demand, "
                "especially for GPUs, networking chips, memory, and foundry capacity."
            ),
            "keyFindings": [
                "AI infrastructure spending can increase demand for semiconductors.",
                "The benefit is uneven across chip designers, foundries, memory, and networking suppliers.",
            ],
            "sources": [{"title": "Deterministic grounded source", "url": "https://example.com/ai-infra"}],
            "webSearchQueries": ["AI infrastructure semiconductor demand"],
            "confidence": "medium",
        }


def setup_case(case: dict[str, Any]) -> EvalEnvironment:
    """Create an isolated database and seed the case fixtures."""
    tmpdir = TemporaryDirectory()
    db.DB_PATH = Path(tmpdir.name) / "advisor-evals.db"
    db.init_db()
    user = db.upsert_user(
        email=f"{case['id']}@example.com",
        google_sub=f"eval-{case['id']}",
        display_name="Eval User",
    )
    if case.get("setup", {}).get("profile") == "moderate":
        profile = MODERATE_PROFILE
        db.save_onboarding_profile(
            user_id=user["id"],
            answers={},
            financial_profile=profile["financialProfile"],
            risk_profile=profile["riskProfile"],
            investor_context=profile["investorContext"],
            summary="Moderate investor profile.",
            confidence="medium",
            missing_flags=[],
            profile_narrative=profile["profileNarrative"],
            profile_synthesis_source="deterministic_fallback",
        )
    for memory_fixture in case.get("setup", {}).get("memory", []):
        seed_memory_fixture(user["id"], memory_fixture)
    return EvalEnvironment(user=user, tmpdir=tmpdir)


def seed_memory_fixture(user_id: str, fixture_name: str) -> None:
    """Seed a named memory fixture."""
    if fixture_name not in {"nvda_ai_infra", "stale_nvda_ai_infra"}:
        raise ValueError(f"Unsupported memory fixture: {fixture_name}")
    is_stale = fixture_name == "stale_nvda_ai_infra"
    date_metadata = {"sourceDate": "2024-01-01T00:00:00+00:00"} if is_stale else {}
    node_properties = {"ticker": "NVDA", "companyName": "Nvidia"}
    if is_stale:
        node_properties["asOfDate"] = "2024-01-01T00:00:00+00:00"
    company = db.upsert_memory_node(
        user_id=user_id,
        node_type="Company",
        external_id="NVDA",
        title="Nvidia (NVDA)",
        summary="Nvidia benefits from AI infrastructure spending through GPU demand.",
        properties=node_properties,
    )
    db.create_memory_chunk(
        user_id=user_id,
        node_id=company["id"],
        source_type="final_synthesis",
        source_id="fixture-nvda-ai-infra",
        text="Nvidia has direct AI infrastructure exposure through data center GPUs and accelerator demand.",
        metadata={"ticker": "NVDA", "theme": "ai infrastructure", **date_metadata},
    )


def fake_tools_for_case(case: dict[str, Any]) -> dict[str, Any]:
    """Return fake tool implementations requested by a fixture."""
    setup = case.get("setup", {})
    return {
        "research_controller": FakeCompanyResearchController() if setup.get("fakeCompanyResearch") else None,
        "generic_harness": FakeGenericResearchHarness() if setup.get("fakeGenericResearch") else None,
    }
