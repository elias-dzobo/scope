"""Tests for the ResearchAgent and its tools.

Tests at two levels:
  1. Tool unit tests — verify StoreEvidence dedup/cap, EvaluateCoverage reporting,
     FinalizeResearch terminal behaviour.
  2. Harness integration tests — mock LLM drives a full gather → evaluate → finalize
     loop; scoring stack is stubbed; assert result shape and evidence state.

Nothing here calls a real LLM, Gemini API, or the database.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent_core.harness import AgentHarness
from agent_core.models import AgentContext, AgentResult, HarnessConfig, TokenUsage
from agent_core.tool import ToolRegistry
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PILLARS = ["Financial Engine", "Valuation"]


def _context(pillars: list[str] | None = None) -> AgentContext:
    pillars = pillars or PILLARS
    return AgentContext(
        user_id="test_user",
        run_id=uuid.uuid4().hex,
        allow_deep_research=True,
        metadata={
            "company_name": "Acme Corp",
            "ticker": "ACME",
            "selected_pillars": pillars,
        },
        state={
            "evidence_by_pillar": defaultdict(list),
            "sources_by_pillar": defaultdict(list),
            "_signal_counts": defaultdict(int),
            "_evidence_fingerprints": set(),
            "finalized": False,
            "finalize_notes": "",
        },
    )


def _research_config(**overrides) -> HarnessConfig:
    base = HarnessConfig(
        model="gpt-4o",
        max_iterations=20,
        max_tool_calls=60,
        timeout_seconds=480.0,
        token_budget=150_000,
        allow_parallel_tools=False,
    )
    return base.model_copy(update=overrides)


def _ai(content: str = "", tool_calls: list[dict] | None = None) -> AIMessage:
    """Build an AIMessage, optionally with tool_calls."""
    kwargs: dict[str, Any] = {"content": content}
    if tool_calls:
        kwargs["tool_calls"] = tool_calls
    return AIMessage(**kwargs)


def _tool_call(name: str, args: dict, call_id: str | None = None) -> dict:
    return {"name": name, "args": args, "id": call_id or uuid.uuid4().hex, "type": "tool_call"}


def _make_mock_llm(responses: list[AIMessage]) -> MagicMock:
    """Build a mock ChatOpenAI that returns responses in sequence."""
    mock = MagicMock()
    mock.bind_tools.return_value = mock
    mock.invoke.side_effect = responses
    mock.model_name = "gpt-4o"
    return mock


# ---------------------------------------------------------------------------
# StoreEvidenceTool — unit tests
# ---------------------------------------------------------------------------


from scope_api.agents.research.tools import (
    EvaluateCoverageTool,
    FinalizeResearchTool,
    SearchWebTool,
    StoreEvidenceTool,
    build_research_tools,
)


class TestStoreEvidenceTool:
    def _tool(self, ctx: AgentContext | None = None) -> StoreEvidenceTool:
        t = StoreEvidenceTool()
        t.set_context(ctx or _context())
        return t

    def test_stores_evidence_item(self):
        t = self._tool()
        result = t._run(
            pillar="Financial Engine",
            signal="Profitability",
            excerpt="ACME reported $1.2B net income in Q3 2025.",
            source_url="https://acme.com/ir",
            source_title="ACME Q3 Earnings",
            confidence=0.6,
        )
        assert "Stored" in result
        ebp = t.ctx.state["evidence_by_pillar"]
        assert len(ebp["Financial Engine"]) == 1
        item = ebp["Financial Engine"][0]
        assert item["signal_name"] == "Profitability"
        assert item["confidence"] == 0.6
        assert item["source_kind"] == "agent_web_search"

    def test_dedup_by_fingerprint(self):
        t = self._tool()
        excerpt = "ACME reported $1.2B net income in Q3 2025."
        t._run(pillar="Financial Engine", signal="Profitability", excerpt=excerpt)
        result = t._run(pillar="Financial Engine", signal="Profitability", excerpt=excerpt)
        assert "Duplicate" in result
        # Only one item stored
        assert len(t.ctx.state["evidence_by_pillar"]["Financial Engine"]) == 1

    def test_per_signal_cap(self):
        t = self._tool()
        for i in range(6):
            t._run(
                pillar="Valuation",
                signal="PE Ratio",
                excerpt=f"ACME P/E ratio point {i}: {20 + i}x trailing twelve months.",
            )
        # 7th call must be rejected
        result = t._run(
            pillar="Valuation",
            signal="PE Ratio",
            excerpt="ACME 7th unique point about PE ratio that is different.",
        )
        assert "already has 6" in result
        assert len(t.ctx.state["evidence_by_pillar"]["Valuation"]) == 6

    def test_cap_is_per_signal_not_per_pillar(self):
        """Two different signals under the same pillar should each get their own cap."""
        t = self._tool()
        for i in range(6):
            t._run(
                pillar="Financial Engine",
                signal="Revenue Growth",
                excerpt=f"ACME revenue grew {10 + i}% YoY in Q{i + 1} 2025.",
            )
        # A different signal should still be accepted
        result = t._run(
            pillar="Financial Engine",
            signal="Profitability",
            excerpt="ACME net margin improved to 18% in FY2025.",
        )
        assert "Stored" in result
        assert len(t.ctx.state["evidence_by_pillar"]["Financial Engine"]) == 7

    def test_source_dedup_by_url(self):
        t = self._tool()
        t._run(
            pillar="Financial Engine",
            signal="Revenue Growth",
            excerpt="Revenue grew 12% YoY in Q3 2025.",
            source_url="https://acme.com/ir",
        )
        t._run(
            pillar="Financial Engine",
            signal="Revenue Growth",
            excerpt="Revenue grew further 15% YoY in Q4 2025.",
            source_url="https://acme.com/ir",  # same URL
        )
        # Source only tracked once
        assert len(t.ctx.state["sources_by_pillar"]["Financial Engine"]) == 1

    def test_excerpt_truncated_at_350(self):
        t = self._tool()
        long_excerpt = "x" * 500
        t._run(pillar="Valuation", signal="DCF", excerpt=long_excerpt)
        item = t.ctx.state["evidence_by_pillar"]["Valuation"][0]
        assert len(item["excerpt"]) == 350


# ---------------------------------------------------------------------------
# EvaluateCoverageTool — unit tests
# ---------------------------------------------------------------------------


class TestEvaluateCoverageTool:
    def _tool(self, ctx: AgentContext | None = None) -> EvaluateCoverageTool:
        t = EvaluateCoverageTool()
        t.set_context(ctx or _context())
        return t

    def test_empty_pillars_shows_empty(self):
        t = self._tool()
        result = t._run(selected_pillars=["Financial Engine", "Valuation"])
        assert "✗ empty" in result
        assert "Financial Engine" in result
        assert "Valuation" in result

    def test_thin_pillar_flagged(self):
        ctx = _context()
        ctx.state["evidence_by_pillar"]["Financial Engine"].append({"excerpt": "one item"})
        t = self._tool(ctx)
        result = t._run(selected_pillars=["Financial Engine"])
        assert "△ thin" in result
        assert "Financial Engine" in result

    def test_strong_pillar_shown(self):
        ctx = _context()
        for i in range(3):
            ctx.state["evidence_by_pillar"]["Valuation"].append({"excerpt": f"item {i}"})
        t = self._tool(ctx)
        result = t._run(selected_pillars=["Valuation"])
        assert "✓ strong" in result

    def test_weak_pillars_listed(self):
        ctx = _context()
        # Financial Engine: 0 items, Valuation: 3 items
        for i in range(3):
            ctx.state["evidence_by_pillar"]["Valuation"].append({"excerpt": f"item {i}"})
        t = self._tool(ctx)
        result = t._run(selected_pillars=["Financial Engine", "Valuation"])
        assert "Pillars needing more research" in result
        assert "Financial Engine" in result

    def test_all_covered_message(self):
        ctx = _context()
        for pillar in PILLARS:
            for i in range(3):
                ctx.state["evidence_by_pillar"][pillar].append({"excerpt": f"item {i}"})
        t = self._tool(ctx)
        result = t._run(selected_pillars=PILLARS)
        assert "Ready to finalize" in result

    def test_total_count_reported(self):
        ctx = _context()
        ctx.state["evidence_by_pillar"]["Financial Engine"].extend([{}, {}, {}])
        ctx.state["evidence_by_pillar"]["Valuation"].extend([{}, {}])
        t = self._tool(ctx)
        result = t._run(selected_pillars=PILLARS)
        assert "Total evidence items: 5" in result


# ---------------------------------------------------------------------------
# FinalizeResearchTool — unit tests
# ---------------------------------------------------------------------------


class TestFinalizeResearchTool:
    def _tool(self, ctx: AgentContext | None = None) -> FinalizeResearchTool:
        t = FinalizeResearchTool()
        t.set_context(ctx or _context())
        return t

    def test_sets_finalized_flag(self):
        t = self._tool()
        t._run(notes="")
        assert t.ctx.state["finalized"] is True

    def test_stores_notes(self):
        t = self._tool()
        t._run(notes="Missing recent 10-K for Valuation pillar.")
        assert "Missing recent 10-K" in t.ctx.state["finalize_notes"]

    def test_summary_includes_pillar_counts(self):
        ctx = _context()
        ctx.state["evidence_by_pillar"]["Financial Engine"].extend([{}, {}, {}])
        ctx.state["evidence_by_pillar"]["Valuation"].extend([{}, {}])
        t = self._tool(ctx)
        result = t._run()
        assert "5 evidence items" in result
        assert "Financial Engine: 3" in result
        assert "Valuation: 2" in result

    def test_max_calls_per_run_is_one(self):
        t = self._tool()
        assert t.max_calls_per_run == 1

    def test_is_terminal_tool(self):
        """ToolRegistry should block a second call via max_calls_per_run=1."""
        from agent_core.guards import check_tool_allowed
        from agent_core.tool import ToolRegistry

        ctx = _context()
        t = FinalizeResearchTool()
        t.set_context(ctx)
        registry = ToolRegistry([t])

        # First call returns None (allowed)
        result = check_tool_allowed("finalize_research", {}, registry, ctx)
        assert result is None

        # Simulate first call completing
        registry.record_call("finalize_research")

        # Second call returns an error string (blocked)
        result = check_tool_allowed("finalize_research", {}, registry, ctx)
        assert result is not None
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# SearchWebTool — unit tests (grounding call mocked)
# ---------------------------------------------------------------------------


class TestSearchWebTool:
    def _tool(self, ctx: AgentContext | None = None) -> SearchWebTool:
        t = SearchWebTool()
        t.set_context(ctx or _context())
        return t

    def _mock_grounded_result(self, n_sources: int = 2, n_candidates: int = 2):
        source = MagicMock()
        source.title = "ACME Q3 Earnings"
        source.url = "https://acme.com/ir"

        candidate = {
            "signal_name": "Profitability",
            "confidence": 0.6,
            "source_title": "ACME Q3 Earnings",
            "excerpt": "ACME reported $1.2B net income.",
        }

        result = MagicMock()
        result.status = "success"
        result.sources = [source] * n_sources
        result.evidence_candidates = [candidate] * n_candidates
        result.answer = ""
        result.error_message = ""
        return result

    def test_successful_search_formats_output(self):
        t = self._tool()
        mock_result = self._mock_grounded_result()
        with (
            patch("research_core.harness.grounding.call_gemini_grounded", return_value=MagicMock()),
            patch("research_core.harness.grounding.parse_grounded_response", return_value=mock_result),
        ):
            output = t._run(query="ACME Q3 earnings", pillar="Financial Engine")
        assert "SEARCH:" in output
        assert "Sources: 2" in output
        assert "ACME Q3 Earnings" in output

    def test_failed_search_returns_error_string(self):
        t = self._tool()
        with patch(
            "research_core.harness.grounding.call_gemini_grounded",
            side_effect=RuntimeError("network error"),
        ):
            output = t._run(query="anything", pillar="Financial Engine")
        assert "Search failed" in output

    def test_unavailable_status_handled(self):
        t = self._tool()
        mock_result = MagicMock()
        mock_result.status = "unavailable"
        mock_result.error_message = "quota exceeded"
        with (
            patch("research_core.harness.grounding.call_gemini_grounded", return_value=MagicMock()),
            patch("research_core.harness.grounding.parse_grounded_response", return_value=mock_result),
        ):
            output = t._run(query="ACME revenue", pillar="Financial Engine")
        assert "unavailable" in output.lower()

    def test_max_calls_per_run_is_24(self):
        t = self._tool()
        assert t.max_calls_per_run == 24


# ---------------------------------------------------------------------------
# build_research_tools factory
# ---------------------------------------------------------------------------


class TestBuildResearchTools:
    def test_returns_four_tools(self):
        tools = build_research_tools()
        names = [t.name for t in tools]
        assert names == ["search_web", "store_evidence", "evaluate_coverage", "finalize_research"]

    def test_all_are_agent_tools(self):
        from agent_core.tool import AgentTool

        tools = build_research_tools()
        for tool in tools:
            assert isinstance(tool, AgentTool)


# ---------------------------------------------------------------------------
# Full harness integration test — mock LLM drives evidence gathering
# ---------------------------------------------------------------------------


class TestResearchHarnessIntegration:
    """
    Drive the harness with a deterministic mock LLM sequence:
      turn 1: search_web (Financial Engine)
      turn 2: store_evidence (2 items)
      turn 3: search_web (Valuation)
      turn 4: store_evidence (2 items)
      turn 5: evaluate_coverage
      turn 6: finalize_research
      turn 7: final answer (no tool calls → loop ends)
    """

    def _build_responses(self) -> list[AIMessage]:
        sid = lambda: uuid.uuid4().hex

        def ai_tool(*calls) -> AIMessage:
            return _ai(tool_calls=list(calls))

        search_fe = _tool_call("search_web", {
            "query": "ACME Corp ACME Financial Engine revenue margins",
            "pillar": "Financial Engine",
            "focus": "annual report",
        })
        store_fe1 = _tool_call("store_evidence", {
            "pillar": "Financial Engine",
            "signal": "Revenue Growth",
            "excerpt": "ACME Corp revenue grew 22% YoY to $4.8B in FY2025.",
            "source_url": "https://acme.com/ar2025",
            "source_title": "ACME Annual Report 2025",
            "confidence": 0.62,
        })
        store_fe2 = _tool_call("store_evidence", {
            "pillar": "Financial Engine",
            "signal": "Profitability",
            "excerpt": "ACME Corp net margin reached 18.5% in FY2025.",
            "source_url": "https://acme.com/ar2025",
            "source_title": "ACME Annual Report 2025",
            "confidence": 0.60,
        })
        search_val = _tool_call("search_web", {
            "query": "ACME Corp ACME Valuation PE ratio DCF",
            "pillar": "Valuation",
            "focus": "analyst report",
        })
        store_val1 = _tool_call("store_evidence", {
            "pillar": "Valuation",
            "signal": "PE Ratio",
            "excerpt": "ACME trades at 24x trailing P/E, below sector median of 28x.",
            "source_url": "https://analyst.example.com/acme",
            "source_title": "ACME Analyst Note",
            "confidence": 0.55,
        })
        store_val2 = _tool_call("store_evidence", {
            "pillar": "Valuation",
            "signal": "DCF Valuation",
            "excerpt": "DCF model suggests $88 fair value vs $72 current price.",
            "source_url": "https://analyst.example.com/acme",
            "source_title": "ACME Analyst Note",
            "confidence": 0.52,
        })
        evaluate = _tool_call("evaluate_coverage", {"selected_pillars": PILLARS})
        finalize = _tool_call("finalize_research", {"notes": "Good coverage across both pillars."})

        return [
            ai_tool(search_fe),
            ai_tool(store_fe1, store_fe2),
            ai_tool(search_val),
            ai_tool(store_val1, store_val2),
            ai_tool(evaluate),
            ai_tool(finalize),
            _ai("Research complete for ACME Corp across Financial Engine and Valuation pillars."),
        ]

    def test_harness_gathers_evidence_and_finalizes(self):
        from scope_api.agents.research.tools import build_research_tools
        from scope_api.agents.research.prompts import RESEARCH_AGENT_SYSTEM_PROMPT, build_research_user_message

        mock_grounded = MagicMock()
        mock_grounded.status = "success"
        mock_grounded.sources = []
        mock_grounded.evidence_candidates = []
        mock_grounded.answer = "ACME is a profitable company."

        mock_llm = _make_mock_llm(self._build_responses())
        ctx = _context()
        user_msg = build_research_user_message(
            company_name="Acme Corp",
            ticker="ACME",
            selected_pillars=PILLARS,
        )

        with (
            patch("agent_core.harness.ChatOpenAI", return_value=mock_llm),
            patch("research_core.harness.grounding.call_gemini_grounded", return_value=MagicMock()),
            patch("research_core.harness.grounding.parse_grounded_response", return_value=mock_grounded),
        ):
            harness = AgentHarness(tools=build_research_tools(), config=_research_config(max_iterations=15))
            result: AgentResult = harness.run(
                system_prompt=RESEARCH_AGENT_SYSTEM_PROMPT,
                user_message=user_msg,
                context=ctx,
            )

        # Evidence should be in context.state
        agent_state = result.metadata.get("agentState") or {}
        ebp = agent_state.get("evidence_by_pillar") or {}
        assert len(ebp.get("Financial Engine", [])) == 2
        assert len(ebp.get("Valuation", [])) == 2

        # finalized flag
        assert agent_state.get("finalized") is True
        assert "Good coverage" in agent_state.get("finalize_notes", "")

        # Result has an answer
        assert result.answer

    def test_harness_result_has_tool_call_records(self):
        from scope_api.agents.research.tools import build_research_tools
        from scope_api.agents.research.prompts import RESEARCH_AGENT_SYSTEM_PROMPT, build_research_user_message

        mock_grounded = MagicMock()
        mock_grounded.status = "success"
        mock_grounded.sources = []
        mock_grounded.evidence_candidates = []
        mock_grounded.answer = ""

        mock_llm = _make_mock_llm(self._build_responses())
        ctx = _context()

        with (
            patch("agent_core.harness.ChatOpenAI", return_value=mock_llm),
            patch("research_core.harness.grounding.call_gemini_grounded", return_value=MagicMock()),
            patch("research_core.harness.grounding.parse_grounded_response", return_value=mock_grounded),
        ):
            harness = AgentHarness(tools=build_research_tools(), config=_research_config(max_iterations=15))
            result: AgentResult = harness.run(
                system_prompt=RESEARCH_AGENT_SYSTEM_PROMPT,
                user_message=build_research_user_message("Acme Corp", "ACME", PILLARS),
                context=ctx,
            )

        # Should have recorded tool calls
        assert len(result.tool_calls) > 0
        tool_names = [tc.tool_name for tc in result.tool_calls]
        assert "search_web" in tool_names
        assert "store_evidence" in tool_names
        assert "finalize_research" in tool_names


# ---------------------------------------------------------------------------
# ResearchAgent.run() — end-to-end with mocked harness and scoring stack
# ---------------------------------------------------------------------------


class TestResearchAgentRun:
    """
    Test the full ResearchAgent.run() method by mocking:
      - AgentHarness.run() to return pre-built evidence state
      - ResearchToolFacade.assess_pillars / build_scorecard
      - FinalSynthesisGenerator.generate
    """

    def _evidence_state(self) -> dict:
        ebp = defaultdict(list)
        ebp["Financial Engine"].extend([
            {"pillar_name": "Financial Engine", "signal_name": "Revenue Growth",
             "excerpt": "Revenue grew 22% YoY.", "confidence": 0.62, "source_url": "https://acme.com"},
            {"pillar_name": "Financial Engine", "signal_name": "Profitability",
             "excerpt": "Net margin 18.5%.", "confidence": 0.60, "source_url": "https://acme.com"},
        ])
        ebp["Valuation"].extend([
            {"pillar_name": "Valuation", "signal_name": "PE Ratio",
             "excerpt": "24x trailing P/E.", "confidence": 0.55, "source_url": "https://acme.com"},
        ])
        sbp = defaultdict(list)
        sbp["Financial Engine"].append({"title": "ACME Annual Report", "url": "https://acme.com", "pillar": "Financial Engine"})
        return {
            "evidence_by_pillar": ebp,
            "sources_by_pillar": sbp,
            "finalized": True,
            "finalize_notes": "All pillars covered.",
            "_signal_counts": defaultdict(int),
            "_evidence_fingerprints": set(),
        }

    def _mock_harness_result(self, state: dict) -> AgentResult:
        return AgentResult(
            answer="Research complete.",
            termination_reason="done",
            tool_calls=[],
            usage=TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500),
            warnings=[],
            trace=[],
            metadata={"agentState": state},
        )

    def test_run_returns_standard_result_shape(self):
        from scope_api.agents.research.agent import ResearchAgent

        fake_state = self._evidence_state()
        fake_harness_result = self._mock_harness_result(fake_state)

        mock_scorecard = {
            "overall_score": 72,
            "recommendation": "Buy",
            "pillar_scores": {},
        }
        mock_assessments = {"Financial Engine": {}, "Valuation": {}}
        mock_synthesis = {"executive_summary": "ACME looks undervalued."}

        with (
            patch("scope_api.agents.research.agent.AgentHarness") as MockHarness,
            patch("scope_api.agents.research.agent.FinalSynthesisGenerator") as MockSynth,
        ):
            mock_h_instance = MockHarness.return_value
            mock_h_instance.run.return_value = fake_harness_result

            mock_s_instance = MockSynth.return_value
            mock_s_instance.generate.return_value = mock_synthesis

            agent = ResearchAgent()
            agent.tools_facade.assess_pillars = MagicMock(return_value=mock_assessments)
            agent.tools_facade.build_scorecard = MagicMock(return_value=mock_scorecard)

            result = agent.run(
                company_name="Acme Corp",
                ticker="ACME",
                selected_pillars=PILLARS,
                run_id="test-run-001",
                user_id="user-001",
            )

        # Required top-level keys
        assert result["ticker"] == "ACME"
        assert result["stock_name"] == "Acme Corp"
        assert result["overall_score"] == 72
        assert result["recommendation"] == "Buy"
        assert "scorecard" in result
        assert "pillar_assessments" in result
        assert "evidence_by_pillar" in result
        assert "sources_by_pillar" in result
        assert "final_synthesis" in result
        assert "runtime_profile" in result
        assert "agent_metadata" in result

    def test_run_returns_compat_fields(self):
        """Fields expected by _build_summary callers must be present."""
        from scope_api.agents.research.agent import ResearchAgent

        fake_state = self._evidence_state()
        fake_harness_result = self._mock_harness_result(fake_state)

        with (
            patch("scope_api.agents.research.agent.AgentHarness") as MockHarness,
            patch("scope_api.agents.research.agent.FinalSynthesisGenerator"),
        ):
            mock_h_instance = MockHarness.return_value
            mock_h_instance.run.return_value = fake_harness_result

            agent = ResearchAgent()
            agent.tools_facade.assess_pillars = MagicMock(return_value={})
            agent.tools_facade.build_scorecard = MagicMock(return_value={"overall_score": 0, "recommendation": "N/A"})

            result = agent.run(company_name="Acme Corp", ticker="ACME", selected_pillars=PILLARS)

        for field in ("query_batch_count", "primary_source_count", "filtered_doc_count",
                      "scraped_doc_count", "documents", "document_tables",
                      "grounded_results", "artifact_manifest"):
            assert field in result, f"Missing compat field: {field}"

    def test_scoring_failure_yields_safe_defaults(self):
        """If assess_pillars raises, result still has a valid shape."""
        from scope_api.agents.research.agent import ResearchAgent

        fake_state = self._evidence_state()
        fake_harness_result = self._mock_harness_result(fake_state)

        with (
            patch("scope_api.agents.research.agent.AgentHarness") as MockHarness,
            patch("scope_api.agents.research.agent.FinalSynthesisGenerator"),
        ):
            MockHarness.return_value.run.return_value = fake_harness_result

            agent = ResearchAgent()
            agent.tools_facade.assess_pillars = MagicMock(side_effect=RuntimeError("scoring exploded"))

            result = agent.run(company_name="Acme Corp", ticker="ACME", selected_pillars=PILLARS)

        assert result["overall_score"] == 0
        assert result["recommendation"] == "Insufficient Data"
        assert result["synthesis_failed"] is True

    def test_agent_metadata_present(self):
        from scope_api.agents.research.agent import ResearchAgent

        fake_state = self._evidence_state()
        fake_harness_result = self._mock_harness_result(fake_state)

        with (
            patch("scope_api.agents.research.agent.AgentHarness") as MockHarness,
            patch("scope_api.agents.research.agent.FinalSynthesisGenerator"),
        ):
            MockHarness.return_value.run.return_value = fake_harness_result

            agent = ResearchAgent()
            agent.tools_facade.assess_pillars = MagicMock(return_value={})
            agent.tools_facade.build_scorecard = MagicMock(return_value={"overall_score": 55, "recommendation": "Hold"})

            result = agent.run(company_name="Acme Corp", ticker="ACME", selected_pillars=PILLARS)

        meta = result["agent_metadata"]
        assert "termination_reason" in meta
        assert "tool_calls" in meta
        assert "token_usage" in meta
        assert "warnings" in meta
        assert "elapsed_seconds" in meta

    def test_evidence_by_pillar_propagated_to_result(self):
        from scope_api.agents.research.agent import ResearchAgent

        fake_state = self._evidence_state()
        fake_harness_result = self._mock_harness_result(fake_state)

        with (
            patch("scope_api.agents.research.agent.AgentHarness") as MockHarness,
            patch("scope_api.agents.research.agent.FinalSynthesisGenerator"),
        ):
            MockHarness.return_value.run.return_value = fake_harness_result

            agent = ResearchAgent()
            agent.tools_facade.assess_pillars = MagicMock(return_value={})
            agent.tools_facade.build_scorecard = MagicMock(return_value={"overall_score": 60, "recommendation": "Hold"})

            result = agent.run(company_name="Acme Corp", ticker="ACME", selected_pillars=PILLARS)

        ebp = result["evidence_by_pillar"]
        assert len(ebp.get("Financial Engine", [])) == 2
        assert len(ebp.get("Valuation", [])) == 1
