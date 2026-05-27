"""ResearchAgent — the agentic replacement for ResearchAgentLoop.

Architecture
------------
The agent loop and the scoring/synthesis pipeline are deliberately separate:

  1. AgentHarness runs the LLM tool-calling loop.
     Tools: search_web, store_evidence, evaluate_coverage, finalize_research.
     The agent gathers evidence and calls finalize_research when satisfied.
     All accumulated evidence lives in AgentContext.state["evidence_by_pillar"].

  2. After the harness exits, ResearchAgent.run() takes the evidence working
     set and pipes it through the existing scoring + synthesis stack:
       - ResearchToolFacade.assess_pillars()
       - ResearchToolFacade.build_scorecard()
       - FinalSynthesisGenerator.generate()

  3. The result dict matches the exact shape that CompanyResearchRunner and
     ResearchAgentLoop return — callers (ResearchController, service layer)
     see no difference.

Separation of concerns:
  Agent   = knows what to look for and when to stop
  Scoring = knows how to judge what was found
  The two never overlap.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Any

from agent_core.harness import AgentHarness
from agent_core.models import AgentContext, AgentResult, HarnessConfig
from research_core.harness.gates import ResearchQualityGates
from research_core.harness.memory import HarnessMemoryStore
from research_core.harness.tools import ResearchToolFacade
from research_core.legacy_pipeline.main import ProgressCallback
from research_core.synthesis.final_synthesis import FinalSynthesisGenerator
from research_core.utils.logger import get_logger

from .prompts import RESEARCH_AGENT_SYSTEM_PROMPT, build_research_user_message
from .tools import build_research_tools

logger = get_logger(__name__)

_DEFAULT_CONFIG = HarnessConfig(
    model="gpt-4o",
    temperature=0.1,
    max_iterations=20,       # evidence gathering can take many rounds
    max_tool_calls=60,       # 24 searches + 36 store_evidence calls + overheads
    timeout_seconds=480.0,   # 8 minutes — matches RESEARCH_RUN_TIMEOUT_SECONDS
    token_budget=150_000,    # research is token-intensive
    allow_parallel_tools=False,  # serial: evidence must be evaluated between searches
)


class ResearchAgent:
    """Agentic company research runner.

    Usage::

        agent = ResearchAgent()
        result = agent.run(
            company_name="Apple Inc.",
            ticker="AAPL",
            selected_pillars=["Financial Engine", "Valuation"],
            progress_callback=callback,
            user_id="u123",
            run_id="r456",
        )
        # result has the same shape as CompanyResearchRunner.run()
    """

    def __init__(
        self,
        tools_facade: ResearchToolFacade | None = None,
        gates: ResearchQualityGates | None = None,
        memory: HarnessMemoryStore | None = None,
        config: HarnessConfig | None = None,
    ) -> None:
        self.tools_facade = tools_facade or ResearchToolFacade()
        self.gates = gates or ResearchQualityGates()
        self.memory = memory or HarnessMemoryStore()
        self.config = config or _DEFAULT_CONFIG
        self._harness = AgentHarness(
            tools=build_research_tools(),
            config=self.config,
        )
        logger.info("ResearchAgent compiled | model=%s", self.config.model)

    def run(
        self,
        *,
        company_name: str,
        ticker: str,
        selected_pillars: list[str] | None = None,
        progress_callback: ProgressCallback | None = None,
        user_financial_profile: dict[str, Any] | None = None,
        user_risk_profile: dict[str, Any] | None = None,
        investor_context: dict[str, Any] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        # These are accepted for API compatibility but not used by the agent runner
        brief: Any = None,
        plan: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Run agentic company research and return the standard result dict."""
        run_id = run_id or uuid.uuid4().hex
        selected_pillars = selected_pillars or list(_DEFAULT_PILLARS)
        t_start = time.monotonic()

        self._emit(progress_callback, "planning", {
            "status": "running", "stage_progress": 10,
            "current_substep": f"starting agentic research for {company_name} ({ticker})",
        })

        # Build per-run context with metadata the tools need
        context = AgentContext(
            user_id=user_id or "",
            run_id=run_id,
            allow_deep_research=True,  # research runs always have permission
            metadata={
                "company_name": company_name,
                "ticker": ticker,
                "selected_pillars": selected_pillars,
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

        user_message = build_research_user_message(
            company_name=company_name,
            ticker=ticker,
            selected_pillars=selected_pillars,
            user_financial_profile=user_financial_profile,
            user_risk_profile=user_risk_profile,
        )

        self._emit(progress_callback, "grounded_research", {
            "status": "running", "stage_progress": 5,
            "current_substep": "agent gathering evidence",
        })

        logger.info(
            "ResearchAgent run started | ticker=%s pillars=%s run_id=%s",
            ticker, selected_pillars, run_id,
        )

        harness_result: AgentResult = self._harness.run(
            system_prompt=RESEARCH_AGENT_SYSTEM_PROMPT,
            user_message=user_message,
            context=context,
        )

        agent_state = harness_result.metadata.get("agentState") or {}
        evidence_by_pillar: dict[str, list[dict]] = dict(
            agent_state.get("evidence_by_pillar") or defaultdict(list)
        )
        sources_by_pillar: dict[str, list[dict]] = dict(
            agent_state.get("sources_by_pillar") or defaultdict(list)
        )
        finalize_notes = agent_state.get("finalize_notes") or ""

        total_evidence = sum(len(v) for v in evidence_by_pillar.values())
        logger.info(
            "ResearchAgent evidence gathered | ticker=%s total=%d pillars=%d reason=%s",
            ticker, total_evidence, len(evidence_by_pillar), harness_result.termination_reason,
        )

        self._emit(progress_callback, "grounded_research", {
            "status": "completed", "stage_progress": 100,
            "current_substep": f"gathered {total_evidence} evidence items across {len(evidence_by_pillar)} pillars",
        })

        # ── Score and synthesize using the existing pipeline ──────────────
        self._emit(progress_callback, "score", {
            "status": "running", "stage_progress": 20,
            "current_substep": "assessing pillars and building scorecard",
        })

        pillar_assessments: dict[str, Any] = {}
        scorecard: dict[str, Any] = {}
        final_synthesis: dict[str, Any] = {}
        synthesis_failed = False

        try:
            pillar_assessments = self.tools_facade.assess_pillars(evidence_by_pillar)
            scorecard = self.tools_facade.build_scorecard(
                company_name, ticker, pillar_assessments, evidence_by_pillar
            )
        except Exception as exc:
            logger.error("Scoring failed | ticker=%s error=%s", ticker, exc)
            synthesis_failed = True
            scorecard = {"overall_score": 0, "recommendation": "Insufficient Data"}
            pillar_assessments = {}

        self._emit(progress_callback, "score", {
            "status": "completed", "stage_progress": 100,
            "current_substep": f"scored: {scorecard.get('overall_score', 0)}/100",
        })

        self._emit(progress_callback, "synthesis", {
            "status": "running", "stage_progress": 20,
            "current_substep": "generating investment memo",
        })

        if not synthesis_failed:
            try:
                synthesis_gen = FinalSynthesisGenerator()
                final_synthesis = synthesis_gen.generate(
                    ticker=ticker,
                    company_name=company_name,
                    scorecard=scorecard,
                    pillar_assessments=pillar_assessments,
                    evidence_by_pillar=evidence_by_pillar,
                    selected_pillars=selected_pillars,
                    user_financial_profile=user_financial_profile,
                    user_risk_profile=user_risk_profile,
                    investor_context=investor_context,
                )
            except Exception as exc:
                logger.error("Final synthesis failed | ticker=%s error=%s", ticker, exc)
                synthesis_failed = True
                final_synthesis = {}

        self._emit(progress_callback, "synthesis", {
            "status": "completed" if not synthesis_failed else "failed",
            "stage_progress": 100,
            "current_substep": "memo complete" if not synthesis_failed else "synthesis failed",
        })

        elapsed = time.monotonic() - t_start

        # Write to harness memory (same as ResearchAgentLoop)
        try:
            self.memory.append_trace(ticker, {
                "event": "agent_run_complete",
                "run_id": run_id,
                "total_evidence": total_evidence,
                "pillar_count": len(evidence_by_pillar),
                "termination_reason": harness_result.termination_reason,
                "elapsed_seconds": round(elapsed, 1),
                "warnings": harness_result.warnings,
            })
        except Exception:
            pass

        return self._build_result(
            ticker=ticker,
            company_name=company_name,
            selected_pillars=selected_pillars,
            evidence_by_pillar=evidence_by_pillar,
            sources_by_pillar=sources_by_pillar,
            pillar_assessments=pillar_assessments,
            scorecard=scorecard,
            final_synthesis=final_synthesis,
            synthesis_failed=synthesis_failed,
            harness_result=harness_result,
            elapsed=elapsed,
            finalize_notes=finalize_notes,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_result(
        self,
        ticker: str,
        company_name: str,
        selected_pillars: list[str],
        evidence_by_pillar: dict[str, list[dict]],
        sources_by_pillar: dict[str, list[dict]],
        pillar_assessments: dict[str, Any],
        scorecard: dict[str, Any],
        final_synthesis: dict[str, Any],
        synthesis_failed: bool,
        harness_result: AgentResult,
        elapsed: float,
        finalize_notes: str,
    ) -> dict[str, Any]:
        """Build the standard result dict expected by ResearchController and the service layer."""
        return {
            "ticker": ticker,
            "stock_name": company_name,
            "selected_pillars": selected_pillars,
            "overall_score": scorecard.get("overall_score", 0),
            "recommendation": scorecard.get("recommendation", "Insufficient Data"),
            "scorecard": scorecard,
            "pillar_assessments": pillar_assessments,
            "evidence_by_pillar": evidence_by_pillar,
            "sources_by_pillar": sources_by_pillar,
            "final_synthesis": final_synthesis,
            "synthesis_failed": synthesis_failed,
            # Standard runtime profile shape
            "runtime_profile": {
                "stock_name": company_name,
                "ticker": ticker,
                "total_duration_seconds": round(elapsed, 1),
                "steps": [
                    {"stage": "agent_evidence_gathering", "status": "completed",
                     "output_summary": {"evidence_count": sum(len(v) for v in evidence_by_pillar.values())}},
                    {"stage": "scoring", "status": "completed" if not synthesis_failed else "failed",
                     "output_summary": {"overall_score": scorecard.get("overall_score", 0)}},
                    {"stage": "synthesis", "status": "completed" if not synthesis_failed else "failed",
                     "output_summary": {}},
                ],
            },
            # Agent-specific metadata
            "agent_metadata": {
                "termination_reason": harness_result.termination_reason,
                "tool_calls": len(harness_result.tool_calls),
                "token_usage": harness_result.usage.model_dump(),
                "warnings": harness_result.warnings,
                "finalize_notes": finalize_notes,
                "elapsed_seconds": round(elapsed, 1),
            },
            # Compatibility fields expected by _build_summary callers
            "query_batch_count": len([tc for tc in harness_result.tool_calls if tc.tool_name == "search_web"]),
            "primary_source_count": sum(len(v) for v in sources_by_pillar.values()),
            "filtered_doc_count": 0,
            "scraped_doc_count": 0,
            "documents": [],
            "document_tables": [],
            "grounded_results": [],
            "artifact_manifest": [],
        }

    @staticmethod
    def _emit(callback: ProgressCallback | None, stage: str, payload: dict[str, Any]) -> None:
        if callback:
            try:
                callback(stage, payload)
            except Exception:
                pass


_DEFAULT_PILLARS = [
    "Macro & Industry",
    "Economic Moat",
    "Financial Engine",
    "Management & Capital Allocation",
    "Valuation",
    "Technical Analysis",
]
