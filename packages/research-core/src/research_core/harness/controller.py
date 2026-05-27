"""Top-level controller for agentic research runs."""

from __future__ import annotations

import os
from typing import Any

from research_core.harness.gates import ResearchQualityGates
from research_core.harness.memory import HarnessMemoryStore
from research_core.harness.models import ResearchPlanStatus
from research_core.harness.planner import ResearchPlanner
from research_core.harness.runner import CompanyResearchRunner, ResearchAgentLoop
from research_core.harness.tools import ResearchToolFacade
from research_core.legacy_pipeline.main import ProgressCallback


def _runner_mode() -> str:
    """Return the configured runner mode.

    Values:
      ``agent2``  — ResearchAgent: fully agentic LLM tool-calling loop (new)
      ``agent``   — ResearchAgentLoop: grounded workstream loop (default)
      ``legacy``  — CompanyResearchRunner: original staged pipeline
    """
    return os.getenv("RESEARCH_RUNNER", "agent").strip().lower()


class ResearchController:
    """Coordinate planning, tool execution, quality gates, and memory.

    Runner selection via ``RESEARCH_RUNNER`` env var:
      ``agent2``  New fully agentic ResearchAgent (LLM decides what to search)
      ``agent``   Grounded workstream loop with retries (default)
      ``legacy``  Original CompanyResearchRunner staged pipeline
    """

    def __init__(
        self,
        planner: ResearchPlanner | None = None,
        tools: ResearchToolFacade | None = None,
        gates: ResearchQualityGates | None = None,
        memory: HarnessMemoryStore | None = None,
    ) -> None:
        self.planner = planner or ResearchPlanner()
        self.tools = tools or ResearchToolFacade()
        self.gates = gates or ResearchQualityGates()
        self.memory = memory or HarnessMemoryStore()

        mode = _runner_mode()
        if mode == "agent2":
            # Import here to avoid circular dep at module load time
            from scope_api.agents.research import ResearchAgent
            self.company_runner = ResearchAgent(
                tools_facade=self.tools,
                gates=self.gates,
                memory=self.memory,
            )
        elif mode == "legacy":
            self.company_runner: Any = CompanyResearchRunner(self.tools, self.gates, self.memory)
        else:
            self.company_runner = ResearchAgentLoop(self.tools, self.gates, self.memory)

    def run_company_research(
        self,
        company_name: str,
        ticker: str,
        selected_pillars: list[str] | None = None,
        progress_callback: ProgressCallback | None = None,
        user_financial_profile: dict[str, Any] | None = None,
        user_risk_profile: dict[str, Any] | None = None,
        investor_context: dict[str, Any] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Run phase-one company research through the harness skeleton."""
        self._emit(
            progress_callback,
            "planning",
            {"status": "running", "stage_progress": 10, "current_substep": "normalizing research brief"},
        )
        brief = self.planner.create_company_brief(
            company_name=company_name,
            ticker=ticker,
            selected_pillars=selected_pillars,
        )
        self._emit(
            progress_callback,
            "planning",
            {"status": "running", "stage_progress": 55, "current_substep": "creating workstream plan"},
        )
        plan = self.planner.create_plan(brief)
        plan.status = ResearchPlanStatus.RUNNING
        self.memory.save_run_state(ticker, brief, plan)
        self._emit(
            progress_callback,
            "planning",
            {
                "status": "completed",
                "stage_progress": 100,
                "current_substep": f"planned {len(plan.workstreams)} research workstreams",
            },
        )

        result = self.company_runner.run(
            brief=brief,
            plan=plan,
            company_name=company_name,
            ticker=ticker,
            selected_pillars=selected_pillars,
            progress_callback=progress_callback,
            user_financial_profile=user_financial_profile,
            user_risk_profile=user_risk_profile,
            investor_context=investor_context,
            run_id=run_id,
            user_id=user_id,
        )

        latest_gate = plan.gate_results[-1] if plan.gate_results else None
        if latest_gate and latest_gate.passed:
            plan.status = ResearchPlanStatus.COMPLETED

        self.memory.save_run_state(ticker, brief, plan, gate_result=latest_gate)
        if plan.observations:
            self.memory.append_trace(ticker, plan.observations[-1])
        if latest_gate:
            self.memory.append_trace(ticker, {"event": "quality_gate", **latest_gate.model_dump(mode="json")})

        # Preserve the existing result contract while adding harness metadata.
        result["harness"] = {
            "brief": brief.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "latest_gate_result": latest_gate.model_dump(mode="json") if latest_gate else None,
        }
        return result

    def _emit(self, progress_callback: ProgressCallback | None, stage: str, payload: dict[str, Any]) -> None:
        """Emit a progress event when the API worker supplied a callback."""
        if progress_callback:
            progress_callback(stage, payload)
