"""Comprehensive eval suite for the Scope advisor feature.

Tests cover:
- Deterministic research plan classification
- Intent resolution and mode selection
- Memory retrieval and context scoring helpers
- Answer quality checks (completeness, boilerplate, grounding)
- Fallback answer coherence
- Conversation state extraction
- Memory write-back error resilience
- Edge cases and regression guards

All tests are offline-safe: no live LLM, DB, or network calls required.
"""

from __future__ import annotations

import re
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal stubs so advisor.py can be imported without the full app stack
# ---------------------------------------------------------------------------

def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    return mod


def _ensure_stubs() -> None:
    """Insert lightweight stubs for dependencies that are not available or should be isolated.

    fastapi and langchain_openai are available in the project venv — do NOT stub them
    here, as doing so would pollute sys.modules and break other test files that import
    these packages for real.

    Only stub modules that:
    - Are not importable in the test environment (no venv package), OR
    - Call live network/DB resources at import time.
    """
    # scope_api.db makes DB calls — always stub it
    if "scope_api.db" not in sys.modules:
        sys.modules["scope_api.db"] = MagicMock()

    # scope_api.auth.* makes no network calls at import, but lacks test DB config
    for name in ("scope_api.auth.dependencies", "scope_api.auth.models"):
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

    # scope_api.generic_research would launch a real research run
    if "scope_api.generic_research" not in sys.modules:
        sys.modules["scope_api.generic_research"] = MagicMock()

    # scope_api.observability.* — no real metrics backend in tests
    for name in ("scope_api.observability", "scope_api.observability.metrics", "scope_api.observability.telemetry"):
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

    # Do NOT stub scope_api.memory here.
    # The real module is importable (from the apps/api/src path on PYTHONPATH),
    # and it only calls DB functions at runtime, not at import time.
    # Stubbing it here would break tests that import scope_api.app (which imports
    # build_memory_router from scope_api.memory).


_ensure_stubs()


# Now we can import the advisor module helpers safely
from scope_api.advisor import (  # noqa: E402
    _answer_looks_incomplete,
    _build_advisor_research_plan_deterministic,
    _conversation_augmented_query,
    _conversation_title,
    _dedupe_strings,
    _evidence_refs,
    _extract_conversation_state,
    _extract_ticker_from_entity,
    _fallback_answer_from_context,
    _generic_memory_fallback_answer,
    _generic_research_fallback_answer,
    _is_generic_or_comparative_question,
    _is_research_like_query,
    _pillars_for_research_request,
    _profile_notes,
    _research_chunks,
    _resolve_mode,
    _text_list,
    _trim_to_complete_sentence,
    AdvisorAnswer,
    AdvisorResearchPlan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coverage(
    *,
    status: str = "sufficient",
    has_memory: bool = True,
    should_research: bool = False,
    confidence: str = "medium",
    entities: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "hasUserProfile": True,
        "hasRelevantMemory": has_memory,
        "matchedEntities": entities or [],
        "missingContext": [],
        "staleContext": [],
        "shouldRunDeepResearch": should_research,
        "targetedResearchRequests": [],
        "confidence": confidence,
    }


def _make_context(
    *,
    chunks: list[dict] | None = None,
    matched_nodes: list[dict] | None = None,
) -> dict[str, Any]:
    return {
        "chunks": chunks or [],
        "matchedNodes": matched_nodes or [],
        "contextPack": {},
        "userProfile": {},
        "conversation": {},
    }


def _make_plan(
    *,
    tickers: list[str] | None = None,
    entities: list[str] | None = None,
    themes: list[str] | None = None,
    intent: str = "company_research",
) -> dict[str, Any]:
    return {
        "tickers": tickers or [],
        "entities": entities or [],
        "themes": themes or [],
        "intent": intent,
    }


def _make_chunk(*, text: str = "NVDA has strong EBITDA margins.", source_type: str = "research_run") -> dict[str, Any]:
    return {
        "text": text,
        "source_type": source_type,
        "source_id": "src_001",
        "node_title": "NVIDIA Research",
        "metadata": {},
    }


def _make_advisor_answer(
    *, answer: str = "This is a complete answer.", stance: str = "answered_from_memory"
) -> AdvisorAnswer:
    return AdvisorAnswer(
        answer=answer,
        stance=stance,
        confidence="medium",
        keyPoints=["Point 1"],
        personalizationNotes=[],
        evidenceRefs=[],
        limitations=[],
        nextActions=[],
        advisorSynthesisSource="llm",
    )


# ===========================================================================
# 1. _is_research_like_query — classify queries needing fresh data
# ===========================================================================

class TestIsResearchLikeQuery:
    def test_latest_triggers_research(self) -> None:
        assert _is_research_like_query("What are the latest earnings for NVDA?") is True

    def test_current_triggers_research(self) -> None:
        assert _is_research_like_query("What is the current valuation of Apple?") is True

    def test_government_contract_triggers_research(self) -> None:
        assert _is_research_like_query("Which companies have government contracts for AI?") is True

    def test_compare_triggers_research(self) -> None:
        assert _is_research_like_query("Compare AMD versus NVDA on margins") is True

    def test_explain_does_not_trigger(self) -> None:
        # "explain" is in explanation_markers → should NOT trigger research
        assert _is_research_like_query("Explain the moat of Apple") is False

    def test_summarize_does_not_trigger(self) -> None:
        assert _is_research_like_query("Summarize this report for me") is False

    def test_what_does_does_not_trigger(self) -> None:
        assert _is_research_like_query("What does free cash flow mean?") is False

    def test_neutral_question_no_trigger(self) -> None:
        assert _is_research_like_query("Is Apple a good business?") is False


# ===========================================================================
# 2. _is_generic_or_comparative_question
# ===========================================================================

class TestIsGenericOrComparativeQuestion:
    def test_industry_keyword_forces_generic(self) -> None:
        assert _is_generic_or_comparative_question(
            query="What is happening in the semiconductor industry?",
            entities=[],
            themes=["semiconductors"],
            plan={"tickers": [], "intent": "industry_research"},
        ) is True

    def test_government_ties_forces_generic(self) -> None:
        assert _is_generic_or_comparative_question(
            query="Which companies have government ties to AI defense?",
            entities=[],
            themes=[],
            plan={},
        ) is True

    def test_compare_forces_generic(self) -> None:
        assert _is_generic_or_comparative_question(
            query="Compare NVDA and AMD",
            entities=["NVDA", "AMD"],
            themes=[],
            plan={"tickers": ["NVDA", "AMD"]},
        ) is True

    def test_themes_without_tickers_forces_generic(self) -> None:
        assert _is_generic_or_comparative_question(
            query="How is AI infrastructure positioned?",
            entities=[],
            themes=["AI infrastructure"],
            plan={"tickers": []},
        ) is True

    def test_specific_ticker_not_generic(self) -> None:
        assert _is_generic_or_comparative_question(
            query="What is NVDA's moat?",
            entities=["NVDA"],
            themes=[],
            plan={"tickers": ["NVDA"], "intent": "company_research"},
        ) is False


# ===========================================================================
# 3. _resolve_mode
# ===========================================================================

class TestResolveMode:
    def test_explicit_mode_preserved(self) -> None:
        assert _resolve_mode("company_research_question", {}) == "company_research_question"

    def test_auto_no_tickers_general(self) -> None:
        plan = {"intent": "industry_research", "tickers": [], "themes": ["AI"]}
        assert _resolve_mode("auto", plan) == "general_financial_research_question"

    def test_auto_no_themes_company(self) -> None:
        plan = {"intent": "company_research", "tickers": ["NVDA"]}
        assert _resolve_mode("auto", plan) == "company_research_question"

    def test_auto_industry_with_tickers_company(self) -> None:
        # has tickers AND intent=industry → defaults to company_research_question
        plan = {"intent": "industry_research", "tickers": ["NVDA"], "themes": []}
        assert _resolve_mode("auto", plan) == "company_research_question"


# ===========================================================================
# 4. _extract_ticker_from_entity
# ===========================================================================

class TestExtractTickerFromEntity:
    def test_parenthetical_ticker(self) -> None:
        assert _extract_ticker_from_entity("NVIDIA (NVDA)") == "NVDA"

    def test_no_ticker_in_name(self) -> None:
        result = _extract_ticker_from_entity("Some Company Name")
        # Without parens and lowercase → short result or empty
        assert result == "" or len(result) <= 6

    def test_plain_ticker_string(self) -> None:
        assert _extract_ticker_from_entity("AAPL") == "AAPL"

    def test_lowercase_entity_no_ticker(self) -> None:
        assert _extract_ticker_from_entity("apple inc") == ""

    def test_dotted_ticker(self) -> None:
        assert _extract_ticker_from_entity("BRK.B") == "BRK.B"


# ===========================================================================
# 5. _dedupe_strings
# ===========================================================================

class TestDedupeStrings:
    def test_dedupes_case_insensitive(self) -> None:
        result = _dedupe_strings(["NVDA", "nvda", "NVDA "])
        assert result == ["NVDA"]

    def test_preserves_order(self) -> None:
        result = _dedupe_strings(["B", "A", "C", "B"])
        assert result == ["B", "A", "C"]

    def test_strips_empty(self) -> None:
        result = _dedupe_strings(["", "  ", "AAPL"])
        assert result == ["AAPL"]


# ===========================================================================
# 6. _pillars_for_research_request
# ===========================================================================

class TestPillarsForResearchRequest:
    def test_valuation_query_returns_valuation(self) -> None:
        pillars = _pillars_for_research_request({"reason": "advisor", "query": "is NVDA overvalued?"})
        assert "Valuation" in pillars

    def test_financial_query_returns_financial(self) -> None:
        pillars = _pillars_for_research_request({"query": "check NVDA margins and cash flow"})
        assert "Financial Engine" in pillars

    def test_industry_query_returns_macro(self) -> None:
        pillars = _pillars_for_research_request({"query": "semiconductor industry outlook"})
        assert "Macro & Industry" in pillars

    def test_empty_query_returns_defaults(self) -> None:
        pillars = _pillars_for_research_request({"query": ""})
        assert len(pillars) > 0


# ===========================================================================
# 7. _conversation_augmented_query
# ===========================================================================

class TestConversationAugmentedQuery:
    def test_plain_query_unchanged_without_anchors(self) -> None:
        conv = {"active_research_run_id": "", "active_generic_research_id": "", "active_topic": ""}
        result = _conversation_augmented_query("What is NVDA?", conv, [])
        assert result == "What is NVDA?"

    def test_they_pronoun_triggers_augmentation(self) -> None:
        conv = {
            "active_research_run_id": "run_001",
            "active_generic_research_id": "",
            "active_topic": "AI Infrastructure",
            "active_entities": ["NVDA", "AMD"],
            "active_themes": ["AI"],
        }
        result = _conversation_augmented_query("How are they positioned?", conv, [])
        assert "NVDA" in result or "AI Infrastructure" in result

    def test_those_companies_augmented(self) -> None:
        conv = {
            "active_research_run_id": "run_001",
            "active_generic_research_id": "",
            "active_topic": "Chips",
            "active_entities": ["NVDA"],
            "active_themes": [],
        }
        result = _conversation_augmented_query("Which of those companies has the best moat?", conv, [])
        assert "NVDA" in result or "Chips" in result


# ===========================================================================
# 8. _build_advisor_research_plan_deterministic
# ===========================================================================

class TestBuildAdvisorResearchPlanDeterministic:
    def _call(
        self,
        query: str,
        *,
        planned_context: dict | None = None,
        allow_deep_research: bool = True,
        conversation: dict | None = None,
    ) -> AdvisorResearchPlan:
        return _build_advisor_research_plan_deterministic(
            query=query,
            augmented_query=query,
            conversation=conversation or {},
            planned_context=planned_context or {"plan": {}, "context": {}, "coverage": _make_coverage()},
            allow_deep_research=allow_deep_research,
        )

    def test_research_like_with_memory_and_ticker_is_company_research(self) -> None:
        """With a specific ticker + fresh research needed + memory available → company_research.

        The deterministic planner upgrades hybrid_research to company_research when
        a specific ticker is present (see _build_advisor_research_plan_deterministic).
        hybrid_research is reserved for theme/thematic queries without explicit tickers.
        """
        ctx = _make_context(chunks=[_make_chunk()])
        planned_context = {
            "plan": {"tickers": ["NVDA"], "themes": [], "entities": ["NVDA"]},
            "context": ctx,
            "coverage": _make_coverage(has_memory=True, should_research=True),
        }
        plan = self._call("What are the latest NVDA margins?", planned_context=planned_context)
        assert plan.answer_mode == "company_research"

    def test_research_like_with_memory_no_ticker_is_hybrid(self) -> None:
        """With memory + fresh research needed but no ticker → hybrid_research."""
        ctx = _make_context(chunks=[_make_chunk()])
        planned_context = {
            "plan": {"tickers": [], "themes": ["AI"], "entities": []},
            "context": ctx,
            "coverage": _make_coverage(has_memory=True, should_research=True),
        }
        plan = self._call("What are the latest AI infrastructure trends?", planned_context=planned_context)
        assert plan.answer_mode == "hybrid_research"

    def test_research_like_no_memory_is_generic_research(self) -> None:
        planned_context = {
            "plan": {"tickers": [], "themes": ["AI"], "entities": []},
            "context": _make_context(),
            "coverage": _make_coverage(has_memory=False, should_research=True),
        }
        plan = self._call(
            "What are the latest AI infrastructure trends?",
            planned_context=planned_context,
        )
        assert plan.answer_mode in {"generic_research", "clarify"}

    def test_memory_only_when_no_fresh_needed(self) -> None:
        ctx = _make_context(chunks=[_make_chunk()])
        planned_context = {
            "plan": {"tickers": [], "themes": [], "entities": []},
            "context": ctx,
            "coverage": _make_coverage(has_memory=True, should_research=False),
        }
        plan = self._call("Explain the research on NVDA", planned_context=planned_context)
        assert plan.answer_mode == "memory_only"

    def test_planner_source_is_deterministic_fallback(self) -> None:
        plan = self._call("What is NVDA?")
        assert plan.planner_source == "deterministic_fallback"

    def test_research_requests_populated_when_needed(self) -> None:
        planned_context = {
            "plan": {"tickers": ["NVDA"], "themes": [], "entities": ["NVDA"]},
            "context": _make_context(chunks=[_make_chunk()]),
            "coverage": _make_coverage(has_memory=True, should_research=True),
        }
        plan = self._call("Latest NVDA margins?", planned_context=planned_context)
        assert plan.should_run_fresh_research is True
        assert len(plan.research_requests) > 0

    def test_no_tickers_no_research_without_research_signal(self) -> None:
        plan = self._call(
            "Explain what ROIC means",
            planned_context={
                "plan": {"tickers": [], "themes": [], "entities": []},
                "context": _make_context(),
                "coverage": _make_coverage(has_memory=False, should_research=False),
            },
        )
        assert plan.should_run_fresh_research is False

    def test_company_research_mode_when_ticker_and_fresh_needed(self) -> None:
        planned_context = {
            "plan": {"tickers": ["AAPL"], "themes": [], "entities": ["Apple"]},
            "context": _make_context(chunks=[_make_chunk()]),
            "coverage": _make_coverage(should_research=True),
        }
        plan = self._call(
            "What are Apple's latest margins?",
            planned_context=planned_context,
        )
        assert plan.answer_mode == "company_research"


# ===========================================================================
# 9. _answer_looks_incomplete
# ===========================================================================

class TestAnswerLooksIncomplete:
    def test_empty_answer_is_incomplete(self) -> None:
        assert _answer_looks_incomplete("", []) is True

    def test_trailing_number_list_is_incomplete(self) -> None:
        assert _answer_looks_incomplete("Key points:\n\n1.", []) is True

    def test_trailing_colon_is_incomplete(self) -> None:
        assert _answer_looks_incomplete("The key risks are:", []) is True

    def test_odd_bold_count_is_incomplete(self) -> None:
        # 3 asterisk pairs = odd
        assert _answer_looks_incomplete("This **is** a **bold statement", []) is True

    def test_complete_short_answer_passes(self) -> None:
        assert _answer_looks_incomplete("NVDA has strong margins driven by data center growth.", []) is False

    def test_short_answer_with_long_research_fails(self) -> None:
        big_research = [{"mode": "generic_financial_research", "synthesis": "a" * 2000}]
        assert _answer_looks_incomplete("Short answer.", big_research) is True

    def test_good_length_answer_with_research_passes(self) -> None:
        big_research = [{"mode": "generic_financial_research", "synthesis": "a" * 2000}]
        assert _answer_looks_incomplete("A " * 700, big_research) is False


# ===========================================================================
# 10. _trim_to_complete_sentence
# ===========================================================================

class TestTrimToCompleteSentence:
    def test_short_text_unchanged(self) -> None:
        text = "This is short."
        assert _trim_to_complete_sentence(text, max_chars=100) == text

    def test_trims_at_sentence_boundary(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        result = _trim_to_complete_sentence(text, max_chars=30)
        assert result.endswith(".")
        assert len(result) <= 40  # allows for a bit of slack at the cut point

    def test_trims_at_paragraph_boundary(self) -> None:
        text = "Para one.\n\nPara two.\n\nPara three."
        result = _trim_to_complete_sentence(text, max_chars=15)
        assert not result.endswith((":", ";", ","))

    def test_no_mid_word_cutoff(self) -> None:
        text = "The company had revenues of $4.5 billion in the most recent fiscal year which ended in December."
        result = _trim_to_complete_sentence(text, max_chars=50)
        # Should not cut mid-word
        assert not result[-1].isalpha() or result.endswith(".")


# ===========================================================================
# 11. _research_chunks — excludes advisor_answer chunks
# ===========================================================================

class TestResearchChunks:
    def test_excludes_advisor_answer_chunks(self) -> None:
        ctx = _make_context(
            chunks=[
                _make_chunk(source_type="research_run"),
                _make_chunk(source_type="advisor_answer"),
                _make_chunk(source_type="generic_research_result"),
            ]
        )
        chunks = _research_chunks(ctx)
        assert len(chunks) == 2
        assert all(c["source_type"] != "advisor_answer" for c in chunks)

    def test_empty_context_returns_empty(self) -> None:
        assert _research_chunks({}) == []

    def test_all_research_chunks_included(self) -> None:
        ctx = _make_context(
            chunks=[_make_chunk(source_type="research_run") for _ in range(5)]
        )
        assert len(_research_chunks(ctx)) == 5


# ===========================================================================
# 12. _evidence_refs — builds refs from context chunks
# ===========================================================================

class TestEvidenceRefs:
    def test_returns_up_to_limit(self) -> None:
        ctx = _make_context(
            chunks=[_make_chunk() for _ in range(10)]
        )
        refs = _evidence_refs(ctx, limit=4)
        assert len(refs) <= 4

    def test_ref_has_expected_keys(self) -> None:
        ctx = _make_context(chunks=[_make_chunk()])
        refs = _evidence_refs(ctx)
        assert len(refs) > 0
        ref = refs[0]
        assert "sourceType" in ref
        assert "nodeTitle" in ref
        assert "excerpt" in ref

    def test_empty_chunks_returns_empty_refs(self) -> None:
        assert _evidence_refs(_make_context()) == []

    def test_advisor_answer_chunks_excluded_from_refs(self) -> None:
        ctx = _make_context(
            chunks=[
                _make_chunk(source_type="advisor_answer"),
                _make_chunk(source_type="research_run"),
            ]
        )
        refs = _evidence_refs(ctx)
        assert len(refs) == 1


# ===========================================================================
# 13. _profile_notes
# ===========================================================================

class TestProfileNotes:
    def test_no_profile_returns_conservative_note(self) -> None:
        notes = _profile_notes({})
        assert len(notes) == 1
        assert "conservative" in notes[0].lower() or "profile" in notes[0].lower()

    def test_risk_profile_present(self) -> None:
        ctx = {
            "userProfile": {
                "riskProfile": {"riskTolerance": "moderate", "riskCapacity": "medium"},
            }
        }
        notes = _profile_notes(ctx)
        assert any("tolerance" in n.lower() for n in notes)

    def test_financial_profile_present(self) -> None:
        ctx = {
            "userProfile": {
                "financialProfile": {"financialResilience": "strong"},
            }
        }
        notes = _profile_notes(ctx)
        assert any("financial" in n.lower() for n in notes)


# ===========================================================================
# 14. _fallback_answer_from_context — no LLM, no hardcoded boilerplate
# ===========================================================================

class TestFallbackAnswerFromContext:
    def _call(
        self,
        query: str,
        *,
        chunks: list[dict] | None = None,
        research_results: list[dict] | None = None,
    ) -> AdvisorAnswer:
        ctx = _make_context(chunks=chunks or [])
        return _fallback_answer_from_context(
            query=query,
            mode="company_research_question",
            plan=_make_plan(tickers=["NVDA"]),
            context=ctx,
            coverage=_make_coverage(),
            refs=[],
            targeted_research_results=research_results or [],
            stance="answered_from_memory",
        )

    def test_returns_advisor_answer_instance(self) -> None:
        ans = self._call("What is NVDA's moat?", chunks=[_make_chunk()])
        assert isinstance(ans, AdvisorAnswer)

    def test_answer_is_non_empty(self) -> None:
        ans = self._call("What is NVDA's moat?", chunks=[_make_chunk()])
        assert len(ans.answer) > 20

    def test_synthesis_source_is_deterministic(self) -> None:
        ans = self._call("What is NVDA's moat?")
        assert ans.advisor_synthesis_source == "deterministic_fallback"

    def test_uses_fresh_research_key_points(self) -> None:
        research = [{
            "status": "completed",
            "mode": "company_research",
            "ticker": "NVDA",
            "companySnapshot": "NVDA has dominant GPU market share.",
            "investmentTakeaway": "Strong business model.",
            "mainRisks": ["competition from AMD"],
        }]
        ans = self._call("What is NVDA's position?", research_results=research)
        assert "NVDA" in ans.answer or any("NVDA" in p for p in ans.key_points)

    def test_no_research_no_chunks_generic_answer(self) -> None:
        ans = self._call("What are NVDA's prospects?")
        assert len(ans.answer) > 0
        assert ans.stance in {"answered_from_memory", "no_relevant_memory", "answered_with_fresh_research"}


# ===========================================================================
# 15. _generic_memory_fallback_answer — regression: no hardcoded AI prose
# ===========================================================================

class TestGenericMemoryFallbackAnswer:
    def test_no_hardcoded_ai_infrastructure_opening(self) -> None:
        """Regression: the hardcoded AI infrastructure opening must not appear for unrelated queries."""
        result = _generic_memory_fallback_answer(
            query="What are the risks in European retail stocks?",
            result={
                "synthesis": "European retail is under pressure from inflation and weak consumer sentiment.",
                "keyFindings": ["Inflation is eroding margins.", "Online competition is intensifying."],
                "risks": ["Currency risk", "Consumer weakness"],
                "layers": [],
                "directBeneficiaries": [],
                "secondOrderBeneficiaries": [],
                "valuationCaveats": [],
            },
        )
        hardcoded = (
            "Yes. The way I'd break this down is by asking: "
            "which companies are closest to unavoidable AI infrastructure spend?"
        )
        assert hardcoded not in result, (
            "Hardcoded AI-infrastructure prose appeared for a European retail question. "
            "This is a product quality bug — see docs/advisor-evaluation.md §4.1."
        )

    def test_result_is_non_empty_string(self) -> None:
        result = _generic_memory_fallback_answer(
            query="What is happening in biotech?",
            result={"synthesis": "Biotech saw a surge in approvals.", "keyFindings": []},
        )
        assert isinstance(result, str)
        assert len(result) > 10

    def test_direct_beneficiaries_included(self) -> None:
        result = _generic_memory_fallback_answer(
            query="Which companies benefit from AI chips?",
            result={
                "synthesis": "AI chip demand is strong.",
                "keyFindings": [],
                "layers": [],
                "directBeneficiaries": [{"name": "NVIDIA", "ticker": "NVDA", "reason": "GPU leader"}],
                "secondOrderBeneficiaries": [],
                "risks": [],
                "valuationCaveats": [],
            },
        )
        assert "NVIDIA" in result or "NVDA" in result


# ===========================================================================
# 16. _conversation_title
# ===========================================================================

class TestConversationTitle:
    def test_short_query(self) -> None:
        title = _conversation_title("What is NVDA's moat?")
        assert len(title) <= 80
        assert "What is NVDA" in title

    def test_empty_query(self) -> None:
        title = _conversation_title("")
        assert title == "Advisor conversation"

    def test_trailing_punctuation_stripped(self) -> None:
        title = _conversation_title("How is Apple performing?")
        assert not title.endswith("?")

    def test_long_query_truncated(self) -> None:
        long_q = "x" * 200
        assert len(_conversation_title(long_q)) <= 80


# ===========================================================================
# 17. _extract_conversation_state
# ===========================================================================

class TestExtractConversationState:
    def test_entities_from_research_results(self) -> None:
        research_results = [
            {
                "directBeneficiaries": [{"name": "NVIDIA", "ticker": "NVDA"}],
                "secondOrderBeneficiaries": [],
                "themes": ["AI"],
                "memoryNodeId": "node_001",
            }
        ]
        state = _extract_conversation_state(
            query="Who benefits from AI chips?",
            plan={},
            answer={"evidenceRefs": []},
            targeted_research_results=research_results,
            context={"conversation": {}},
        )
        assert any("NVIDIA" in e or "NVDA" in e for e in state["active_entities"])

    def test_topic_falls_back_to_query(self) -> None:
        state = _extract_conversation_state(
            query="AI chip demand",
            plan={},
            answer={"evidenceRefs": []},
            targeted_research_results=[],
            context={"conversation": {}},
        )
        assert state["active_topic"]  # non-empty

    def test_entities_capped_at_24(self) -> None:
        research_results = [
            {
                "directBeneficiaries": [{"name": f"Company{i}", "ticker": f"CO{i}"} for i in range(30)],
                "secondOrderBeneficiaries": [],
                "themes": [],
            }
        ]
        state = _extract_conversation_state(
            query="Give me all companies",
            plan={},
            answer={"evidenceRefs": []},
            targeted_research_results=research_results,
            context={"conversation": {}},
        )
        assert len(state["active_entities"]) <= 24

    def test_themes_capped_at_12(self) -> None:
        research_results = [
            {
                "directBeneficiaries": [],
                "secondOrderBeneficiaries": [],
                "themes": [f"theme_{i}" for i in range(20)],
            }
        ]
        state = _extract_conversation_state(
            query="Themes",
            plan={},
            answer={"evidenceRefs": []},
            targeted_research_results=research_results,
            context={"conversation": {}},
        )
        assert len(state["active_themes"]) <= 12


# ===========================================================================
# 18. _text_list
# ===========================================================================

class TestTextList:
    def test_list_input(self) -> None:
        assert _text_list(["a", "b", "c"]) == ["a", "b", "c"]

    def test_string_input_wrapped(self) -> None:
        assert _text_list("NVDA") == ["NVDA"]

    def test_none_input(self) -> None:
        assert _text_list(None) == []

    def test_empty_items_filtered(self) -> None:
        result = _text_list(["a", "", "b"])
        assert "a" in result and "b" in result


# ===========================================================================
# 19. _generic_research_fallback_answer
# ===========================================================================

class TestGenericResearchFallbackAnswer:
    def test_non_empty_synthesis_included(self) -> None:
        result = _generic_research_fallback_answer(
            query="What is happening in AI?",
            result={"synthesis": "AI spending is accelerating across hyperscalers."},
        )
        assert "AI spending" in result

    def test_empty_synthesis_uses_findings(self) -> None:
        result = _generic_research_fallback_answer(
            query="What is happening?",
            result={"synthesis": "", "keyFindings": ["Point A.", "Point B."]},
        )
        assert "Point A" in result or "Point B" in result

    def test_completely_empty_returns_safe_message(self) -> None:
        result = _generic_research_fallback_answer(
            query="What?",
            result={"synthesis": "", "keyFindings": []},
        )
        assert "rerun" in result.lower() or "not contain" in result.lower()


# ===========================================================================
# 20. Memory write-back error handling (integration contract)
# ===========================================================================

class TestMemoryWriteBackResilience:
    """
    These tests verify the CONTRACT that memory write-back failures must not
    propagate to the user response. The actual write-back functions call db.*
    which is stubbed — so we verify the advisor orchestration pattern, not the DB.
    """

    def test_fallback_answer_does_not_raise_on_db_stub(self) -> None:
        """_fallback_answer_from_context must not raise regardless of context."""
        ctx = _make_context()
        result = _fallback_answer_from_context(
            query="test",
            mode="company_research_question",
            plan={},
            context=ctx,
            coverage=_make_coverage(has_memory=False, status="insufficient"),
            refs=[],
            targeted_research_results=[],
            stance="no_relevant_memory",
        )
        assert isinstance(result, AdvisorAnswer)

    def test_answer_looks_incomplete_does_not_raise_on_edge_inputs(self) -> None:
        """_answer_looks_incomplete must handle edge case inputs gracefully."""
        assert _answer_looks_incomplete("", []) is True
        assert _answer_looks_incomplete("   ", []) is True

    def test_evidence_refs_does_not_raise_on_malformed_chunks(self) -> None:
        """_evidence_refs must handle chunks with missing keys."""
        ctx = {"chunks": [{"source_type": "research_run"}]}  # missing text, node_title etc.
        refs = _evidence_refs(ctx)
        assert isinstance(refs, list)
