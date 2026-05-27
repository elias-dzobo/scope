"""Tests for the first auth and run-ownership slice."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from scope_api import db
from scope_api.app import app
from scope_api.advisor import AdvisorRunRequest, run_advisor_harness
from scope_api.application.run_service import ResearchLimitExceededError, ResearchRunService
from scope_api.auth.dependencies import require_current_user
from scope_api.auth.google import verify_google_id_token
from scope_api.auth.jwt import create_access_token, decode_access_token
from scope_api.auth.models import AuthUser
from scope_api.config import ProductionConfigError, load_api_config
from scope_api.generic_research import GenericFinancialResearchHarness, GenericResearchSynthesizer
from scope_api.memory import build_context_retrieval_plan, index_completed_research_run, plan_user_context, retrieve_user_context
from scope_api.onboarding import synthesize_onboarding_profile, synthesize_profile_narrative
from research_core.harness.models import GroundedResearchResult, GroundedSource


def test_jwt_round_trip(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("JWT_ISSUER", "scope-test")
    monkeypatch.setenv("JWT_AUDIENCE", "scope-web-test")

    token = create_access_token(user_id="user-123", email="u@example.com")
    payload = decode_access_token(token)

    assert payload["sub"] == "user-123"
    assert payload["email"] == "u@example.com"
    assert payload["iss"] == "scope-test"
    assert payload["aud"] == "scope-web-test"


def test_dev_google_token_verification(monkeypatch):
    monkeypatch.setenv("AUTH_ALLOW_DEV_GOOGLE_TOKEN", "true")
    credential = json.dumps(
        {
            "sub": "google-123",
            "email": "u@example.com",
            "name": "User Example",
            "picture": "https://example.com/avatar.png",
        }
    )

    identity = verify_google_id_token(credential)

    assert identity.google_sub == "google-123"
    assert identity.email == "u@example.com"
    assert identity.display_name == "User Example"


def test_user_upsert_updates_last_login(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()

    first = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="Old")
    second = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="New")

    assert first["id"] == second["id"]
    assert second["display_name"] == "New"


def test_cookie_session_round_trip_and_revoke(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")

    session_token, session = db.create_user_session(user_id=user["id"], user_agent="pytest")
    resolved = db.get_user_by_session_token(session_token)

    assert session["user_id"] == user["id"]
    assert resolved["id"] == user["id"]
    assert db.revoke_user_session(session_token) is True
    assert db.get_user_by_session_token(session_token) is None


def test_auth_routes_set_and_revoke_session_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ALLOW_DEV_GOOGLE_TOKEN", "true")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    credential = json.dumps({"sub": "google-123", "email": "u@example.com", "name": "User Example"})

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/google", json={"credential": credential})
        assert login.status_code == 200
        assert "scope_session" in client.cookies

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "u@example.com"

        logout = client.post("/api/v1/auth/logout")
        assert logout.status_code == 200
        assert "scope_session" not in client.cookies
        assert client.get("/api/v1/auth/me").status_code == 401


def test_onboarding_profile_synthesis_and_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    answers = {
        "country": "ghana",
        "monthlyIncomeBand": "3000_to_7000",
        "incomePredictability": "stable",
        "monthlyDisposableBand": "comfortable",
        "emergencyFundMonths": "3_to_6",
        "debtStress": "none",
        "majorExpenses": "no",
        "dependents": "none",
        "primaryGoal": "wealth_building",
        "timeHorizon": "5_to_10",
        "priorInvesting": "stocks_etfs",
        "lossReaction": "hold",
        "yearDownReaction": "manageable",
        "cryptoComfort": "small_only",
        "priority": "balanced",
        "checkFrequency": "monthly",
        "volatileStockResponse": "small",
        "lifestyleLossCapacity": "moderate",
        "knowledgeLevel": "comfortable",
        "preferredMarkets": ["us_stocks"],
        "investingStyle": "occasional_research",
        "preferredCompanies": ["stable_profitable"],
        "restrictedSectors": [],
        "conservativeDefault": "balanced",
    }

    derived = synthesize_onboarding_profile(answers)
    saved = db.save_onboarding_profile(
        user_id=user["id"],
        answers=answers,
        financial_profile=derived["financialProfile"],
        risk_profile=derived["riskProfile"],
        investor_context=derived["investorContext"],
        summary=derived["summary"],
        confidence=derived["confidence"],
        missing_flags=derived["missingFlags"],
        profile_narrative=derived["profileNarrative"],
        profile_synthesis_source=derived["profileSynthesisSource"],
    )

    assert saved["financial_profile"]["financialResilience"] in {"moderate", "high"}
    assert saved["risk_profile"]["riskTolerance"] in {"moderate", "high"}
    assert saved["profile_narrative"]["headline"]
    assert db.get_onboarding_profile(user["id"])["summary"]
    updated = db.save_onboarding_profile(
        user_id=user["id"],
        answers={**answers, "monthlyDisposableBand": "high"},
        financial_profile=derived["financialProfile"],
        risk_profile=derived["riskProfile"],
        investor_context=derived["investorContext"],
        summary=derived["summary"],
        confidence=derived["confidence"],
        missing_flags=derived["missingFlags"],
        profile_narrative=derived["profileNarrative"],
        profile_synthesis_source=derived["profileSynthesisSource"],
    )
    assert saved["profile_version"] == "v1"
    assert updated["profile_version"] == "v2"


def test_research_run_captures_profile_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    derived = synthesize_onboarding_profile(
        {
            "country": "ghana",
            "incomePredictability": "stable",
            "monthlyDisposableBand": "comfortable",
            "emergencyFundMonths": "3_to_6",
            "debtStress": "none",
            "majorExpenses": "no",
            "dependents": "none",
            "primaryGoal": "wealth_building",
            "timeHorizon": "5_to_10",
            "priorInvesting": "stocks_etfs",
            "lossReaction": "hold",
            "yearDownReaction": "manageable",
            "cryptoComfort": "small_only",
            "priority": "balanced",
            "volatileStockResponse": "small",
            "knowledgeLevel": "comfortable",
        }
    )
    db.save_onboarding_profile(
        user_id=user["id"],
        answers={},
        financial_profile=derived["financialProfile"],
        risk_profile=derived["riskProfile"],
        investor_context=derived["investorContext"],
        summary=derived["summary"],
        confidence=derived["confidence"],
        missing_flags=derived["missingFlags"],
        profile_narrative=derived["profileNarrative"],
        profile_synthesis_source=derived["profileSynthesisSource"],
    )

    db.create_run("run-1", "Apple Inc.", "AAPL", ["Valuation"], user_id=user["id"])
    run = db.get_run("run-1", user_id=user["id"])

    assert run["profile_snapshot"]["userId"] == user["id"]
    assert run["profile_snapshot"]["financialProfile"]["financialResilience"] in {"moderate", "high"}
    assert run["profile_snapshot_captured_at"]


def test_artifact_manifest_records_are_run_owned(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    db.create_run("run-1", "Apple Inc.", "AAPL", ["Valuation"], user_id=user["id"])
    artifact = db.create_artifact_record(
        run_id="run-1",
        user_id=user["id"],
        ticker="AAPL",
        artifact_type="final_synthesis",
        storage_backend="local",
        storage_uri="/tmp/a.json",
        content_type="application/json",
        size_bytes=10,
        checksum="abc",
        metadata={"kind": "memo"},
    )

    rows = db.list_run_artifacts("run-1", user_id=user["id"])

    assert artifact["artifact_type"] == "final_synthesis"
    assert rows[0]["storage_uri"] == "/tmp/a.json"
    assert rows[0]["metadata"]["kind"] == "memo"
    assert db.list_run_artifacts("run-1", anonymous_only=True) == []


def test_completed_research_run_indexes_user_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    db.save_onboarding_profile(
        user_id=user["id"],
        answers={},
        financial_profile={"financialResilience": "moderate"},
        risk_profile={"riskTolerance": "moderate"},
        investor_context={"primaryGoal": "wealth_building"},
        summary="Moderate investor profile.",
        confidence="medium",
        missing_flags=[],
        profile_narrative={"headline": "Moderate investor profile."},
        profile_synthesis_source="deterministic_fallback",
    )
    db.create_run("run-1", "Apple Inc.", "AAPL", ["Financial Engine"], user_id=user["id"])
    result_payload = {
        "summary": {
            "ticker": "AAPL",
            "stock_name": "Apple Inc.",
            "overall_score": 82,
            "recommendation": "Buy",
        },
        "scorecard": {"overall_score": 82, "recommendation": "Buy"},
        "pillarAssessments": {"Financial Engine": {"score": 85}},
        "finalSynthesis": {
            "companySnapshot": "Apple sells iPhones, Macs, services, and wearables.",
            "investmentTakeaway": "Apple remains profitable with resilient cash generation.",
            "recommendationRationale": ["Strong margins", "Large cash flow"],
            "mainRisks": ["Premium valuation"],
            "bottomLine": "Useful candidate for a patient investor.",
            "personalizedRecommendation": {
                "finalRecommendation": {"action": "Buy", "explanation": "Fits a moderate investor."}
            },
            "pillarTakeaways": [
                {
                    "pillarName": "Financial Engine",
                    "score": 85,
                    "plainEnglishSummary": "Apple consistently generates profit and cash.",
                    "position": "supportive",
                    "whyItMatters": "Profits fund reinvestment and buybacks.",
                    "supportingPoints": ["High margin services revenue"],
                    "watchItems": ["iPhone growth"],
                    "technicalDetails": "Strong free cash flow profile.",
                }
            ],
        },
    }
    db.update_run_state("run-1", status="completed", result=result_payload, completed=True)
    artifact = db.create_artifact_record(
        run_id="run-1",
        user_id=user["id"],
        ticker="AAPL",
        artifact_type="final_synthesis",
        storage_backend="local",
        storage_uri="/tmp/final.json",
    )

    stats = index_completed_research_run(
        run=db.get_run("run-1", user_id=user["id"]),
        result_payload=result_payload,
        artifacts=[artifact],
    )
    context = retrieve_user_context(user["id"], "cash generation Apple", limit=5)
    company_nodes = db.list_memory_nodes(user["id"], node_type="Company")

    assert stats["nodes"] >= 5
    assert stats["chunks"] >= 2
    assert company_nodes[0]["external_id"] == "AAPL"
    assert db.list_memory_nodes(user["id"], node_type="InvestorPreference")
    assert context["coverage"]["hasMemory"] is True
    assert any("cash" in chunk["text"].lower() for chunk in context["chunks"])


def test_anonymous_research_run_is_not_indexed_into_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    db.create_run("run-1", "Apple Inc.", "AAPL", ["Valuation"])
    run = db.get_run("run-1")

    stats = index_completed_research_run(run=run, result_payload={"summary": {"ticker": "AAPL"}}, artifacts=[])

    assert stats["skipped"] == 1


def test_context_retrieval_plan_identifies_entities_and_gaps(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")

    plan = build_context_retrieval_plan(
        user["id"],
        "How does AI infrastructure spending affect Nvidia and AMD?",
    )
    result = plan_user_context(
        user["id"],
        "How does AI infrastructure spending affect Nvidia and AMD?",
    )

    assert plan.intent == "industry_research"
    assert {"NVDA", "AMD"}.issubset(set(plan.tickers))
    assert "theme_context" in plan.needed_context
    assert result["coverage"]["shouldRunDeepResearch"] is True
    assert "retrievable_evidence" in result["coverage"]["missingContext"]
    assert result["coverage"]["targetedResearchRequests"]


def test_context_retrieval_plan_uses_llm_with_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")

    class FakeStructured:
        def invoke(self, _messages):
            return {
                "query": "What are the risks in Ghana's beverage sector?",
                "intent": "industry_research",
                "entities": [],
                "tickers": ["IPO"],
                "themes": ["beverage sector", "ghana"],
                "neededContext": ["user_profile", "industry_context", "recent_sources", "theme_context"],
                "searchQueries": ["Ghana beverage sector risks", "Kasapreko IPO beverage market"],
                "freshnessRequirement": "high",
                "reasoning": "The question is thematic and needs recent industry context.",
                "plannerSource": "llm",
            }

    class FakeChat:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(self, _schema, method=None):
            return FakeStructured()

    monkeypatch.setattr("scope_api.memory.ChatOpenAI", FakeChat)

    plan = build_context_retrieval_plan(user["id"], "What are the risks in Ghana's beverage sector?")

    assert plan.intent == "industry_research"
    assert plan.planner_source == "llm"
    assert plan.tickers == []
    assert "beverage sector" in plan.themes
    assert plan.freshness_requirement == "high"


def test_context_coverage_passes_when_memory_matches_query(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    db.save_onboarding_profile(
        user_id=user["id"],
        answers={},
        financial_profile={"financialResilience": "high"},
        risk_profile={"riskTolerance": "moderate"},
        investor_context={"primaryGoal": "wealth_building"},
        summary="Moderate investor profile.",
        confidence="medium",
        missing_flags=[],
        profile_narrative={"headline": "Moderate investor profile."},
        profile_synthesis_source="deterministic_fallback",
    )
    company = db.upsert_memory_node(
        user_id=user["id"],
        node_type="Company",
        external_id="NVDA",
        title="Nvidia (NVDA)",
        summary="Nvidia benefits from AI infrastructure spending through GPU demand.",
        properties={"ticker": "NVDA", "companyName": "Nvidia"},
    )
    db.create_memory_chunk(
        user_id=user["id"],
        node_id=company["id"],
        source_type="final_synthesis",
        source_id="run-nvda",
        text="Nvidia has direct AI infrastructure exposure through data center GPUs and accelerator demand.",
        metadata={"ticker": "NVDA", "theme": "ai infrastructure"},
    )

    result = plan_user_context(user["id"], "Does AI infrastructure help NVDA?", limit=5)

    assert result["plan"]["tickers"] == ["NVDA"]
    assert result["coverage"]["status"] in {"sufficient", "partial"}
    assert result["coverage"]["shouldRunDeepResearch"] is False
    assert result["context"]["userProfile"]["riskProfile"]["riskTolerance"] == "moderate"
    assert result["context"]["contextPack"]["profileContext"]["riskProfile"]["riskTolerance"] == "moderate"
    assert result["context"]["contextPack"]["researchContext"][0]["retrievalScore"] > 0
    assert "answers" not in result["context"]["contextPack"]["profileContext"]


def test_context_coverage_requests_refresh_for_stale_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setenv("SCOPE_MEMORY_STALE_DAYS", "30")
    monkeypatch.setenv("SCOPE_THEMATIC_MEMORY_STALE_DAYS", "30")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    db.save_onboarding_profile(
        user_id=user["id"],
        answers={},
        financial_profile={"financialResilience": "high"},
        risk_profile={"riskTolerance": "moderate"},
        investor_context={"primaryGoal": "wealth_building"},
        summary="Moderate investor profile.",
        confidence="medium",
        missing_flags=[],
        profile_narrative={"headline": "Moderate investor profile."},
        profile_synthesis_source="deterministic_fallback",
    )
    company = db.upsert_memory_node(
        user_id=user["id"],
        node_type="Company",
        external_id="NVDA",
        title="Nvidia (NVDA)",
        summary="Nvidia benefits from AI infrastructure spending through GPU demand.",
        properties={
            "ticker": "NVDA",
            "companyName": "Nvidia",
            "asOfDate": "2024-01-01T00:00:00+00:00",
        },
    )
    db.create_memory_chunk(
        user_id=user["id"],
        node_id=company["id"],
        source_type="final_synthesis",
        source_id="run-nvda",
        text="Nvidia has direct AI infrastructure exposure through data center GPUs and accelerator demand.",
        metadata={
            "ticker": "NVDA",
            "theme": "ai infrastructure",
            "sourceDate": "2024-01-01T00:00:00+00:00",
        },
    )

    result = plan_user_context(user["id"], "Does AI infrastructure help NVDA?", limit=5)

    assert result["coverage"]["hasRelevantMemory"] is True
    assert result["coverage"]["shouldRunDeepResearch"] is True
    assert result["coverage"]["staleContext"]
    assert result["coverage"]["targetedResearchRequests"]
    assert result["context"]["freshness"]["thresholdDays"] == 30
    assert result["context"]["freshness"]["staleItems"][0]["ageDays"] > 30


def test_context_retrieval_ranks_fresh_relevant_memory_first(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setenv("SCOPE_MEMORY_STALE_DAYS", "30")
    monkeypatch.setenv("SCOPE_THEMATIC_MEMORY_STALE_DAYS", "30")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    stale = db.upsert_memory_node(
        user_id=user["id"],
        node_type="Company",
        external_id="NVDA-stale",
        title="Nvidia stale",
        summary="Old Nvidia AI infrastructure context.",
        properties={"ticker": "NVDA", "asOfDate": "2024-01-01T00:00:00+00:00"},
    )
    fresh = db.upsert_memory_node(
        user_id=user["id"],
        node_type="Company",
        external_id="NVDA-fresh",
        title="Nvidia fresh",
        summary="Fresh Nvidia AI infrastructure context.",
        properties={"ticker": "NVDA", "asOfDate": "2026-05-01T00:00:00+00:00"},
    )
    db.create_memory_chunk(
        user_id=user["id"],
        node_id=stale["id"],
        source_type="final_synthesis",
        source_id="stale",
        text="Nvidia AI infrastructure demand helped data center GPU growth.",
        metadata={"ticker": "NVDA", "theme": "ai infrastructure", "sourceDate": "2024-01-01T00:00:00+00:00"},
    )
    db.create_memory_chunk(
        user_id=user["id"],
        node_id=fresh["id"],
        source_type="final_synthesis",
        source_id="fresh",
        text="Nvidia AI infrastructure demand helped data center GPU growth.",
        metadata={"ticker": "NVDA", "theme": "ai infrastructure", "sourceDate": "2026-05-01T00:00:00+00:00"},
    )

    context = retrieve_user_context(user["id"], "Does AI infrastructure help NVDA?", limit=5)

    assert context["chunks"][0]["source_id"] == "fresh"
    assert context["chunks"][0]["retrievalScore"] > context["chunks"][1]["retrievalScore"]


def test_advisor_harness_answers_from_memory_and_saves_trace(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    db.save_onboarding_profile(
        user_id=user["id"],
        answers={},
        financial_profile={"financialResilience": "high"},
        risk_profile={"riskTolerance": "moderate", "riskCapacity": "moderate"},
        investor_context={"primaryGoal": "wealth_building"},
        summary="Moderate investor profile.",
        confidence="medium",
        missing_flags=[],
        profile_narrative={"headline": "Moderate investor profile."},
        profile_synthesis_source="deterministic_fallback",
    )
    company = db.upsert_memory_node(
        user_id=user["id"],
        node_type="Company",
        external_id="NVDA",
        title="Nvidia (NVDA)",
        summary="Nvidia benefits from AI infrastructure spending.",
        properties={"ticker": "NVDA", "companyName": "Nvidia"},
    )
    db.create_memory_chunk(
        user_id=user["id"],
        node_id=company["id"],
        source_type="final_synthesis",
        source_id="run-nvda",
        text="Nvidia benefits from AI infrastructure because data center GPUs support accelerator demand.",
        metadata={"ticker": "NVDA", "theme": "ai infrastructure"},
    )

    result = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(query="Does AI infrastructure help NVDA?"),
    )
    saved = db.get_advisor_run(result["id"], user["id"])
    advisor_nodes = db.list_memory_nodes(user["id"], node_type="AdvisorRun")

    assert result["status"] == "completed"
    assert result["answer"]["stance"] == "answered_from_memory"
    assert result["coverage"]["shouldRunDeepResearch"] is False
    assert any(step["step"] == "coverage_gate" for step in result["trace"])
    assert saved["answer"]["confidence"] in {"medium", "high"}
    assert advisor_nodes[0]["external_id"] == result["id"]


def test_advisor_harness_does_not_use_prior_advisor_answer_as_evidence(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    db.save_onboarding_profile(
        user_id=user["id"],
        answers={},
        financial_profile={"financialResilience": "moderate"},
        risk_profile={"riskTolerance": "moderate", "riskCapacity": "moderate"},
        investor_context={"primaryGoal": "wealth_building"},
        summary="Moderate investor profile.",
        confidence="medium",
        missing_flags=[],
        profile_narrative={"headline": "Moderate investor profile."},
        profile_synthesis_source="deterministic_fallback",
    )
    company = db.upsert_memory_node(
        user_id=user["id"],
        node_type="Company",
        external_id="KAS",
        title="Kasapreko PLC (KAS)",
        summary="Kasapreko is a Ghanaian beverage company.",
        properties={"ticker": "KAS", "companyName": "Kasapreko PLC"},
    )
    db.create_memory_chunk(
        user_id=user["id"],
        node_id=company["id"],
        source_type="advisor_answer",
        source_id="advisor-old",
        text="Help me understand the KAS research and what the biggest risks currently are Based on saved Scope research...",
        metadata={"ticker": "KAS"},
    )
    db.create_memory_chunk(
        user_id=user["id"],
        node_id=company["id"],
        source_type="final_synthesis",
        source_id="run-kas",
        text="Kasapreko sells beverages in Ghana. Main risks include IPO execution, limited public trading history, and reliance on prospectus assumptions.",
        metadata={"ticker": "KAS", "section": "final_synthesis"},
    )

    result = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(query="Help me understand the KAS research and the potential risks"),
    )

    assert result["answer"]["stance"] == "answered_from_memory"
    assert "Based on saved Scope research" not in result["answer"]["answer"]
    assert "IPO execution" in result["answer"]["answer"] or "IPO execution" in " ".join(result["answer"]["keyPoints"])
    assert all(ref["sourceType"] != "advisor_answer" for ref in result["answer"]["evidenceRefs"])


def test_advisor_harness_uses_llm_synthesizer_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    db.save_onboarding_profile(
        user_id=user["id"],
        answers={},
        financial_profile={"financialResilience": "high"},
        risk_profile={"riskTolerance": "moderate", "riskCapacity": "moderate"},
        investor_context={"primaryGoal": "wealth_building"},
        summary="Moderate investor profile.",
        confidence="medium",
        missing_flags=[],
        profile_narrative={"headline": "Moderate investor profile."},
        profile_synthesis_source="deterministic_fallback",
    )
    company = db.upsert_memory_node(
        user_id=user["id"],
        node_type="Company",
        external_id="NVDA",
        title="Nvidia (NVDA)",
        summary="Nvidia benefits from AI infrastructure spending.",
        properties={"ticker": "NVDA", "companyName": "Nvidia"},
    )
    db.create_memory_chunk(
        user_id=user["id"],
        node_id=company["id"],
        source_type="final_synthesis",
        source_id="run-nvda",
        text="Nvidia data center GPUs are exposed to AI infrastructure spending.",
        metadata={"ticker": "NVDA"},
    )

    class FakeStructured:
        def invoke(self, _messages):
            return {
                "answer": "The saved research says AI infrastructure spending is a real demand driver for Nvidia, but valuation still matters.",
                "stance": "model_supplied_stance",
                "confidence": "medium",
                "keyPoints": ["AI infrastructure demand supports data center GPU sales."],
                "personalizationNotes": [],
                "evidenceRefs": [],
                "limitations": ["This is based on saved research only."],
                "nextActions": ["Review valuation before acting."],
            }

    class FakeChat:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(self, _schema, method=None):
            assert method == "function_calling"
            return FakeStructured()

    monkeypatch.setattr("scope_api.advisor.ChatOpenAI", FakeChat)

    result = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(query="Does AI infrastructure help NVDA?"),
    )

    assert result["answer"]["answer"].startswith("The saved research says AI infrastructure")
    assert result["answer"]["stance"] == "answered_from_memory"
    assert result["answer"]["evidenceRefs"]


def test_advisor_harness_defers_when_research_gaps_exist(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")

    result = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(query="How does AI infrastructure spending affect Nvidia and AMD?"),
    )

    assert result["status"] == "completed"
    assert result["answer"]["stance"] == "needs_research"
    assert result["coverage"]["shouldRunDeepResearch"] is True
    assert result["researchRequests"]
    assert any(step["step"] == "research_gap_executor" and step["status"] == "deferred" for step in result["trace"])


def test_advisor_harness_runs_targeted_company_research_when_allowed(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    db.save_onboarding_profile(
        user_id=user["id"],
        answers={},
        financial_profile={"financialResilience": "moderate"},
        risk_profile={"riskTolerance": "moderate", "riskCapacity": "moderate"},
        investor_context={"primaryGoal": "wealth_building"},
        summary="Moderate investor profile.",
        confidence="medium",
        missing_flags=[],
        profile_narrative={"headline": "Moderate investor profile."},
        profile_synthesis_source="deterministic_fallback",
    )

    class FakeResearchController:
        def run_company_research(self, **kwargs):
            return {
                "ticker": kwargs["ticker"],
                "stock_name": kwargs["company_name"],
                "scorecard": {"overall_score": 78, "recommendation": "Watchlist"},
                "final_synthesis": {
                    "companySnapshot": "Nvidia sells GPUs for data centers and AI infrastructure.",
                    "investmentTakeaway": "AI infrastructure spending is a direct demand driver for Nvidia.",
                    "mainRisks": ["Valuation could already reflect high growth expectations."],
                    "sourceNote": "Generated from targeted company research.",
                },
            }

    result = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(
            query="How does AI infrastructure spending affect Nvidia and AMD?",
            allow_deep_research=True,
        ),
        research_controller=FakeResearchController(),
    )
    targeted_nodes = db.list_memory_nodes(user["id"], node_type="TargetedResearchResult")

    assert result["answer"]["stance"] == "answered_with_fresh_research"
    assert result["answer"]["targetedResearchResults"][0]["ticker"] == "NVDA"
    assert result["answer"]["targetedResearchResults"][0]["overallScore"] == 78
    assert any(step["step"] == "research_gap_executor" and step["status"] == "completed" for step in result["trace"])
    assert targeted_nodes


def test_advisor_harness_runs_generic_research_without_tickers(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")

    class FakeGenericHarness:
        def run(self, **kwargs):
            return {
                "status": "completed",
                "query": kwargs["query"],
                "mode": "generic_financial_research",
                "themes": ["ai infrastructure", "semiconductors"],
                "synthesis": "AI infrastructure spending can increase demand for semiconductors, especially GPUs and networking chips.",
                "keyFindings": [
                    "AI infrastructure spending can increase demand for semiconductors.",
                    "The benefit is uneven across chip designers, foundries, memory, and networking suppliers.",
                ],
                "sources": [{"title": "Grounded source", "url": "https://example.com"}],
                "webSearchQueries": ["AI infrastructure semiconductor demand"],
                "confidence": "medium",
            }

    result = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(
            query="How does AI infrastructure spending affect semiconductor stocks?",
            allow_deep_research=True,
        ),
        generic_harness=FakeGenericHarness(),
    )
    generic_nodes = db.list_memory_nodes(user["id"], node_type="GenericResearchResult")
    theme_nodes = db.list_memory_nodes(user["id"], node_type="Theme")

    assert result["answer"]["stance"] == "answered_with_fresh_research"
    assert result["answer"]["targetedResearchResults"][0]["mode"] == "generic_financial_research"
    assert result["answer"]["targetedResearchResults"][0]["themes"] == ["ai infrastructure", "semiconductors"]
    assert any(step["step"] == "research_gap_executor" and step["status"] == "completed" for step in result["trace"])
    assert generic_nodes
    assert {node["external_id"] for node in theme_nodes} == {"ai infrastructure", "semiconductors"}


def test_generic_research_harness_retries_weak_workstreams():
    class FakeTools:
        def __init__(self):
            self.calls = 0

        def run_grounded_workstream_research(self, brief, workstream):
            self.calls += 1
            if self.calls == 1:
                return GroundedResearchResult(
                    workstream_id=workstream.id,
                    pillar_name=workstream.pillar_name,
                    query=workstream.goal,
                    answer="Semiconductor demand is linked to AI infrastructure spending.",
                    sources=[GroundedSource(source_id="s1", title="One source", url="https://example.com/1")],
                    evidence_candidates=[],
                    citation_supports=[],
                )
            return GroundedResearchResult(
                workstream_id=workstream.id,
                pillar_name=workstream.pillar_name,
                query=workstream.goal,
                answer="AI infrastructure spending supports demand for GPUs, networking chips, memory, and foundry capacity.",
                sources=[
                    GroundedSource(source_id="s1", title="Source one", url="https://example.com/1"),
                    GroundedSource(source_id="s2", title="Source two", url="https://example.com/2"),
                ],
                evidence_candidates=[{"excerpt": "AI capex supports semiconductor demand."}],
                citation_supports=[{"text": "AI capex supports semiconductor demand.", "source_ids": ["s1"]}],
            )

    harness = GenericFinancialResearchHarness(tools=FakeTools())
    result = harness.run(
        query="How does AI infrastructure spending affect semiconductor stocks?",
        plan={"intent": "industry_research", "themes": ["ai infrastructure", "semiconductors"], "tickers": []},
        research_requests=[{"reason": "retrievable_evidence", "query": "AI infrastructure semiconductor stocks", "targets": []}],
    )

    assert result["status"] == "completed"
    assert result["researchPlan"]["workstreamCount"] == 1
    assert result["workstreamResults"][0]["coverage"]["retried"] is True
    assert result["workstreamResults"][0]["coverage"]["status"] == "sufficient"
    assert result["openQuestions"] == []
    assert result["synthesisSource"] == "deterministic_fallback"
    assert "AI infrastructure spending supports demand" in result["synthesis"]


def test_generic_research_key_findings_preserve_decimals_and_skip_partial_markdown():
    text = (
        "The AI infrastructure market is projected to grow from $23.5 billion to over $309 billion by 2031.\n\n"
        "**1. Semiconductors and AI accelerators**\n\n"
        "NVIDIA, AMD, TSMC, and Broadcom are strategically positioned across chips, foundry, and custom silicon."
    )

    findings = GenericFinancialResearchHarness._key_findings(text)

    assert findings[0] == "The AI infrastructure market is projected to grow from $23.5 billion to over $309 billion by 2031."
    assert "$23." not in findings
    assert "5 billion" not in findings
    assert "**1." not in findings


def test_generic_research_synthesizer_uses_llm_when_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class FakeStructured:
        def invoke(self, _messages):
            return {
                "answer": (
                    "AI infrastructure beneficiaries cluster into chips, cloud infrastructure, networking, "
                    "and power systems. Direct beneficiaries include NVIDIA and TSMC because they sit close "
                    "to accelerator demand. Second-order beneficiaries include data center and power suppliers "
                    "that benefit as deployment scales. Valuation still matters because thematic exposure can "
                    "already be priced into the stock."
                )
                * 3,
                "keyFindings": ["AI infrastructure demand supports chips, cloud, networking, and power."],
                "layers": [
                    {
                        "name": "Semiconductors",
                        "explanation": "AI workloads require accelerators and advanced foundry capacity.",
                        "companies": [
                            {
                                "name": "NVIDIA",
                                "ticker": "NVDA",
                                "reason": "Dominant accelerator supplier.",
                                "evidenceStrength": "high",
                            }
                        ],
                        "risks": ["Export controls and competition."],
                    }
                ],
                "directBeneficiaries": [
                    {
                        "name": "NVIDIA",
                        "ticker": "NVDA",
                        "reason": "Direct accelerator exposure.",
                        "evidenceStrength": "high",
                    }
                ],
                "secondOrderBeneficiaries": [],
                "risks": ["Capex cycles can reverse."],
                "valuationCaveats": ["Theme strength does not automatically mean attractive valuation."],
                "sourceNotes": ["Grounded source coverage was available."],
                "confidence": "medium",
            }

    class FakeChat:
        def __init__(self, **_kwargs):
            pass

        def with_structured_output(self, _schema, method=None):
            return FakeStructured()

    monkeypatch.setattr("scope_api.generic_research.ChatOpenAI", FakeChat)

    synthesis = GenericResearchSynthesizer().generate(
        query="Which AI infrastructure stocks benefit?",
        plan={"themes": ["ai infrastructure"]},
        grounded_results=[{"answer": "NVIDIA and TSMC benefit.", "sources": []}],
        sources=[{"title": "Source", "url": "https://example.com"}],
        open_questions=[],
        fallback_text="Fallback answer about AI infrastructure.",
    )

    assert synthesis.synthesis_source == "llm"
    assert synthesis.direct_beneficiaries[0].ticker == "NVDA"
    assert synthesis.layers[0].name == "Semiconductors"


def test_advisor_generic_fallback_uses_complete_fresh_synthesis(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    long_synthesis = (
        "The AI infrastructure supply chain has several investable layers.\n\n"
        "**1. Semiconductors and accelerators**\n"
        "NVIDIA, AMD, TSMC, Broadcom, and memory suppliers benefit when model training and inference demand rises.\n\n"
        "**2. Cloud and data centers**\n"
        "Amazon, Microsoft, Google, Equinix, and Digital Realty benefit when AI workloads need more hosted compute.\n\n"
        "**3. Networking, power, and cooling**\n"
        "Arista, Cisco, Ciena, Eaton, and Bloom Energy can benefit from higher-density AI clusters."
    )

    class FakeGenericHarness:
        def run(self, **kwargs):
            return {
                "status": "completed",
                "query": kwargs["query"],
                "mode": "generic_financial_research",
                "themes": ["ai infrastructure"],
                "synthesis": long_synthesis,
                "keyFindings": ["**1."],
                "sources": [{"title": "Grounded source", "url": "https://example.com"}],
                "webSearchQueries": ["AI infrastructure supply chain stocks"],
                "confidence": "medium",
            }

    result = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(
            query="Which AI infrastructure supply chain stocks can benefit from the AI boom?",
            allow_deep_research=True,
        ),
        generic_harness=FakeGenericHarness(),
    )

    answer = result["answer"]["answer"]
    assert result["answer"]["stance"] == "answered_with_fresh_research"
    assert "Semiconductors and accelerators" in answer
    assert "Networking, power, and cooling" in answer
    assert not answer.rstrip().endswith("**1.")


def test_advisor_followup_breaks_down_saved_generic_research(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    result = {
        "query": "AI infrastructure supply chain beneficiaries",
        "mode": "generic_financial_research",
        "themes": ["ai infrastructure"],
        "synthesis": "AI infrastructure beneficiaries cluster across chips, cloud, networking, power, and cooling.",
        "keyFindings": ["AI infrastructure spend benefits several supply-chain layers."],
        "layers": [
            {
                "name": "Semiconductors and accelerators",
                "explanation": "AI workloads need GPUs, custom accelerators, memory, and advanced foundry capacity.",
                "companies": [
                    {"name": "NVIDIA", "ticker": "NVDA", "reason": "Direct accelerator exposure."},
                    {"name": "TSMC", "ticker": "TSM", "reason": "Advanced foundry exposure."},
                ],
            },
            {
                "name": "Networking and data centers",
                "explanation": "Large AI clusters need switching, optical networking, colocation, and cloud capacity.",
                "companies": [
                    {"name": "Arista Networks", "ticker": "ANET", "reason": "AI data center networking."},
                    {"name": "Equinix", "ticker": "EQIX", "reason": "Colocation demand."},
                ],
            },
        ],
        "directBeneficiaries": [
            {"name": "NVIDIA", "ticker": "NVDA", "reason": "Dominant accelerator supplier."},
            {"name": "TSMC", "ticker": "TSM", "reason": "Manufactures leading-edge AI chips."},
        ],
        "secondOrderBeneficiaries": [
            {"name": "Eaton", "ticker": "ETN", "reason": "Power and cooling exposure."},
        ],
        "risks": ["AI capex could slow."],
        "valuationCaveats": ["Strong positioning can already be priced in."],
        "confidence": "medium",
    }
    node = db.upsert_memory_node(
        user_id=user["id"],
        node_type="GenericResearchResult",
        external_id="generic-ai-infra",
        title=result["query"],
        summary=result["synthesis"],
        properties=result,
    )
    db.create_memory_chunk(
        user_id=user["id"],
        node_id=node["id"],
        source_type="generic_research_result",
        source_id=node["id"],
        text=" ".join([result["query"], result["synthesis"], "NVIDIA TSMC Arista Eaton"]),
        metadata={"themes": ["ai infrastructure"], "confidence": "medium"},
    )

    response = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(query="break this result down. what companies should i be on the lookout for ?"),
    )
    answer = response["answer"]["answer"]

    assert response["answer"]["stance"] == "answered_from_memory"
    assert "Where to look" in answer
    assert "NVIDIA" in answer
    assert "TSMC" in answer
    assert "Based on saved Scope research" not in answer
    assert "looking at the entire" not in answer


def test_advisor_conversation_preserves_thread_state_for_followups(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")

    class FakeGenericHarness:
        def run(self, **kwargs):
            return {
                "status": "completed",
                "query": kwargs["query"],
                "mode": "generic_financial_research",
                "themes": ["ai infrastructure"],
                "synthesis": "AI infrastructure supply chain winners include NVIDIA, TSMC, Arista, and Eaton.",
                "keyFindings": ["AI infrastructure supply chain winners include NVIDIA, TSMC, Arista, and Eaton."],
                "layers": [
                    {
                        "name": "AI supply chain",
                        "explanation": "Strategic suppliers span chips, networking, and power.",
                        "companies": [
                            {"name": "NVIDIA", "ticker": "NVDA", "reason": "Accelerators."},
                            {"name": "TSMC", "ticker": "TSM", "reason": "Foundry capacity."},
                        ],
                    }
                ],
                "directBeneficiaries": [
                    {"name": "NVIDIA", "ticker": "NVDA", "reason": "Accelerators."},
                    {"name": "TSMC", "ticker": "TSM", "reason": "Foundry capacity."},
                ],
                "secondOrderBeneficiaries": [{"name": "Eaton", "ticker": "ETN", "reason": "Power equipment."}],
                "sources": [{"title": "Grounded source", "url": "https://example.com"}],
                "confidence": "medium",
            }

    first = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(query="Research AI infrastructure supply chain companies", allowDeepResearch=True),
        generic_harness=FakeGenericHarness(),
    )
    conversation_id = first["conversationId"]
    second = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(
            query="Do any of the companies they listed have ties with the US government?",
            conversationId=conversation_id,
            allowDeepResearch=False,
        ),
    )
    conversation = db.get_advisor_conversation(conversation_id, user["id"])
    messages = db.list_advisor_messages(conversation_id, user["id"])

    assert second["conversationId"] == conversation_id
    assert conversation["active_entities"]
    assert any("NVIDIA" in entity for entity in conversation["active_entities"])
    assert len(messages) == 4
    assert "Kasapreko" not in second["answer"]["answer"]
    assert second["trace"][1]["payload"]["threadAugmented"] is True


def test_advisor_followup_runs_generic_research_for_comparative_thread_question(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    conversation = db.create_advisor_conversation(
        user_id=user["id"],
        title="AI supply chain",
        active_topic="AI infrastructure supply chain",
        active_entities=["NVIDIA (NVDA)", "TSMC (TSM)", "Eaton (ETN)"],
        active_themes=["ai infrastructure", "semiconductors"],
    )

    calls = []

    class FakeGenericHarness:
        def run(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "completed",
                "query": kwargs["query"],
                "mode": "generic_financial_research",
                "themes": ["ai infrastructure", "government demand"],
                "synthesis": "NVIDIA, TSMC, and Eaton have different levels of US government exposure through chips, foundry capacity, power equipment, or infrastructure programs.",
                "keyFindings": [
                    "NVIDIA may have exposure through federal AI and defense-adjacent compute demand.",
                    "TSMC is tied to US industrial policy through semiconductor manufacturing incentives.",
                    "Eaton can benefit from power infrastructure demand connected to data center buildouts.",
                ],
                "directBeneficiaries": [
                    {"name": "NVIDIA", "ticker": "NVDA", "reason": "AI compute supplier."},
                    {"name": "TSMC", "ticker": "TSM", "reason": "Advanced foundry capacity."},
                    {"name": "Eaton", "ticker": "ETN", "reason": "Power equipment."},
                ],
                "sources": [{"title": "Grounded source", "url": "https://example.com"}],
                "confidence": "medium",
            }

    result = run_advisor_harness(
        user_id=user["id"],
        payload=AdvisorRunRequest(
            query="Do any of the companies they listed have ties with the US government?",
            conversationId=conversation["id"],
            allowDeepResearch=True,
        ),
        generic_harness=FakeGenericHarness(),
    )

    assert calls
    assert calls[0]["plan"]["tickers"] == []
    assert result["answer"]["stance"] == "answered_with_fresh_research"
    assert result["answer"]["targetedResearchResults"][0]["mode"] == "generic_financial_research"
    assert "US government" in result["answer"]["targetedResearchResults"][0]["query"]
    assert any(step["step"] == "advisor_research_planner" and step["payload"]["forceGenericResearch"] for step in result["trace"])


def test_advisor_routes_create_list_and_get_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    app.dependency_overrides[require_current_user] = lambda: AuthUser.from_row(user)
    client = TestClient(app)
    try:
        created = client.post(
            "/api/v1/advisor/runs",
            json={"query": "Explain NVDA from saved memory", "allowDeepResearch": False},
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["id"]
        assert payload["query"] == "Explain NVDA from saved memory"
        assert "answer" in payload

        listed = client.get("/api/v1/advisor/runs")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        fetched = client.get(f"/api/v1/advisor/runs/{payload['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == payload["id"]
    finally:
        app.dependency_overrides.clear()


def test_advisor_conversation_can_seed_completed_research_run(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    db.create_run(
        run_id="run-kas",
        company_name="Kasapreko PLC",
        ticker="KAS",
        selected_pillars=["Financial Engine"],
        user_id=user["id"],
    )
    app.dependency_overrides[require_current_user] = lambda: AuthUser.from_row(user)
    client = TestClient(app)
    try:
        created = client.post(
            "/api/v1/advisor/conversations",
            json={"activeResearchRunId": "run-kas"},
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["activeResearchRunId"] == "run-kas"
        assert payload["activeEntities"] == ["KAS", "Kasapreko PLC"]
        assert payload["messages"] == []

        run = client.post(
            "/api/v1/advisor/runs",
            json={
                "conversationId": payload["id"],
                "query": "Help me understand this research.",
                "allowDeepResearch": False,
            },
        )
        assert run.status_code == 200
        run_payload = run.json()
        assert run_payload["conversationId"] == payload["id"]
        assert "Active research run id: run-kas" in run_payload["plan"]["query"]

        fetched = client.get(f"/api/v1/advisor/conversations/{payload['id']}")
        assert fetched.status_code == 200
        assert len(fetched.json()["messages"]) == 2
    finally:
        app.dependency_overrides.clear()


def test_postgres_backend_requires_database_url(monkeypatch):
    monkeypatch.setenv("SCOPE_DB_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    try:
        db.init_db()
    except RuntimeError as exc:
        assert "DATABASE_URL is required" in str(exc)
    else:
        raise AssertionError("Expected Postgres backend to require DATABASE_URL")


def test_production_config_rejects_unsafe_defaults(monkeypatch):
    monkeypatch.setenv("SCOPE_ENV", "production")
    monkeypatch.delenv("SCOPE_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("SCOPE_REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("AUTH_ALLOW_DEV_GOOGLE_TOKEN", "true")
    monkeypatch.setenv("SCOPE_DB_BACKEND", "sqlite")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "local")
    monkeypatch.delenv("ARTIFACT_BUCKET", raising=False)

    try:
        load_api_config()
    except ProductionConfigError as exc:
        message = str(exc)
        assert "SCOPE_ALLOWED_ORIGINS is required" in message
        assert "AUTH_ALLOW_DEV_GOOGLE_TOKEN=false" in message
        assert "SCOPE_DB_BACKEND=postgres" in message
        assert "ARTIFACT_STORE_BACKEND=minio or s3" in message
    else:
        raise AssertionError("Expected unsafe production config to fail")


def test_production_config_accepts_required_settings(monkeypatch):
    monkeypatch.setenv("SCOPE_ENV", "production")
    monkeypatch.setenv("SCOPE_ALLOWED_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("SCOPE_REQUIRE_AUTH", "true")
    monkeypatch.setenv("AUTH_ALLOW_DEV_GOOGLE_TOKEN", "false")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("OBSERVABILITY_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("EXA_API_KEY", "exa-test")
    monkeypatch.setenv("SCOPE_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://scope:secret@localhost:5432/scope")
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "minio")
    monkeypatch.setenv("ARTIFACT_BUCKET", "scope-artifacts")
    monkeypatch.setenv("ARTIFACT_S3_ENDPOINT_URL", "http://127.0.0.1:9000")

    config = load_api_config()

    assert config.is_production is True
    assert config.allowed_origins == ["https://app.example.com"]
    assert config.require_auth is True


def test_research_routes_require_auth_when_configured(monkeypatch):
    app.state.config.require_auth = True
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/research-runs")
        assert response.status_code == 401
    finally:
        app.state.config.require_auth = False


def test_request_body_size_limit(monkeypatch):
    previous = app.state.config.max_body_bytes
    app.state.config.max_body_bytes = 8
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/auth/google", content="x" * 32)
        assert response.status_code == 413
    finally:
        app.state.config.max_body_bytes = previous


def test_research_daily_limit_blocks_submission(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setenv("RESEARCH_DAILY_LIMIT_PER_USER", "1")
    monkeypatch.setenv("RESEARCH_MAX_ACTIVE_RUNS_PER_USER", "10")
    db.init_db()
    user = db.upsert_user(email="u@example.com", google_sub="g-1", display_name="User")
    db.create_run("existing-run", "Nvidia", "NVDA", ["Macro & Industry"], user_id=user["id"])

    class FakeOrchestrator:
        def submit(self, **_kwargs):
            return True

    service = ResearchRunService(orchestrator=FakeOrchestrator())
    try:
        service.submit_run("AMD", "AMD", ["Macro & Industry"], user_id=user["id"])
    except ResearchLimitExceededError as exc:
        assert "Daily research limit reached" in str(exc)
    else:
        raise AssertionError("Expected daily research limit to block submission")


def test_onboarding_profile_narrative_uses_llm_when_available(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class FakeStructured:
        def invoke(self, _messages):
            return {
                "headline": "Moderate investor profile.",
                "financialSummary": "Financial base is developing but usable.",
                "riskSummary": "Risk tolerance and capacity are both moderate.",
                "recommendationGuidance": ["Keep position sizing modest."],
                "cautions": ["Emergency fund is still important."],
                "confidenceNote": "Confidence is medium.",
            }

    class FakeChat:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(self, _schema):
            return FakeStructured()

    monkeypatch.setattr("scope_api.onboarding.ChatOpenAI", FakeChat)
    narrative, source = synthesize_profile_narrative(
        answers={},
        financial_profile={"financialResilience": "moderate", "emergencyFundStrength": "healthy", "debtBurden": "low"},
        risk_profile={"riskTolerance": "moderate", "riskCapacity": "moderate"},
        investor_context={"personalizationNotes": ["Balanced default."]},
        confidence="medium",
        missing_flags=[],
        fallback_summary="Fallback.",
    )

    assert source == "llm"
    assert narrative["headline"] == "Moderate investor profile."
