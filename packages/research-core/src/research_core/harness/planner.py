"""Planning utilities for the research harness.

The first planner is deterministic for the known company-research path, but the
output schema is not deterministic-only. Future LLM or human planners can create
the same `ResearchPlan` shape with different workstreams.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from research_core.harness.models import (
    DEFAULT_COMPANY_PILLARS,
    EvidenceRequirement,
    ResearchBrief,
    ResearchEntity,
    ResearchMode,
    ResearchPlan,
    ResearchWorkstream,
    StopConditions,
)


RESEARCH_PLANNER_MODEL = os.getenv("RESEARCH_PLANNER_MODEL", "gpt-4o-mini")

RESEARCH_PLANNER_SYSTEM = """You are a research planning agent for an investment research harness.

Create source-grounded workstreams that fit the user's research mode.
For company equity research, cover every selected six-pillar framework pillar exactly once.
Adapt search focus and goals for pre-IPO, fixed-income, non-US listings, weak ticker data, and industry-style requests.
Return only structured workstreams; deterministic validators will enforce schema and limits."""


class PlannedWorkstream(BaseModel):
    """LLM-authored workstream draft."""

    id: str = ""
    title: str
    pillar_name: str = Field(default="", alias="pillarName")
    goal: str
    search_focus: list[str] = Field(default_factory=list, alias="searchFocus")
    open_gaps: list[str] = Field(default_factory=list, alias="openGaps")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchPlanDraft(BaseModel):
    """LLM-authored research plan draft."""

    workstreams: list[PlannedWorkstream] = Field(default_factory=list)
    planner_reasoning: str = Field(default="", alias="plannerReasoning")
    instrument_status: str = Field(default="unknown", alias="instrumentStatus")


PILLAR_EVIDENCE_REQUIREMENTS: dict[str, list[EvidenceRequirement]] = {
    "Macro & Industry": [
        EvidenceRequirement(
            name="industry growth and demand",
            description="Market growth, demand drivers, regulation, or secular trend evidence.",
            preferred_source_types=["industry_report", "financial_data", "investor_relations"],
        ),
        EvidenceRequirement(name="competitive landscape", description="Market structure or competitor evidence."),
    ],
    "Economic Moat": [
        EvidenceRequirement(name="competitive advantage", description="Durable moat signal such as brand, patents, cost advantage, or network effect."),
        EvidenceRequirement(name="customer or contract durability", description="Evidence of retention, contracts, switching costs, or market share."),
    ],
    "Financial Engine": [
        EvidenceRequirement(name="recent revenue and profitability", description="Revenue, EPS, margin, EBITDA, or operating income evidence.", preferred_source_types=["regulatory", "investor_relations"]),
        EvidenceRequirement(name="cash flow and balance sheet", description="Free cash flow, leverage, debt, or liquidity evidence.", preferred_source_types=["regulatory", "investor_relations"]),
    ],
    "Management & Capital Allocation": [
        EvidenceRequirement(name="capital allocation", description="Buybacks, dividends, acquisitions, reinvestment, or allocation priorities.", preferred_source_types=["investor_relations", "regulatory"]),
        EvidenceRequirement(name="governance and ownership", description="Board, insider ownership, governance, or management incentives."),
    ],
    "Valuation": [
        EvidenceRequirement(name="valuation multiples", description="P/E, EV/EBITDA, price target, peer multiple, or historical multiple evidence."),
        EvidenceRequirement(name="guidance or intrinsic value inputs", description="Forward guidance, DCF inputs, margin assumptions, or upside/downside evidence.", preferred_source_types=["investor_relations", "financial_data"]),
    ],
    "Technical Analysis": [
        EvidenceRequirement(name="trend and momentum", description="Moving averages, RSI, breakout, price trend, or relative performance."),
        EvidenceRequirement(name="volume and levels", description="Volume, support/resistance, accumulation, or institutional activity."),
    ],
}


PILLAR_SEARCH_FOCUS: dict[str, list[str]] = {
    "Macro & Industry": ["industry reports", "market growth", "regulation", "competitor dynamics"],
    "Economic Moat": ["annual reports", "customer retention", "market share", "competitive advantage"],
    "Financial Engine": ["annual report", "quarterly results", "earnings presentation", "cash flow"],
    "Management & Capital Allocation": ["proxy statement", "governance", "dividends", "buybacks", "acquisitions"],
    "Valuation": ["guidance", "valuation multiples", "peer comparison", "price targets"],
    "Technical Analysis": ["price trend", "volume", "moving averages", "relative strength"],
}


def _dedupe(values: list[str]) -> list[str]:
    """Return non-empty strings in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = value.strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out


class ResearchPlanner:
    """Create research plans from normalized briefs."""

    def create_company_brief(
        self,
        company_name: str,
        ticker: str,
        selected_pillars: list[str] | None = None,
    ) -> ResearchBrief:
        """Build the phase-one brief used for six-pillar company research."""
        normalized_ticker = ticker.strip().upper()
        pillars = [pillar.strip() for pillar in (selected_pillars or DEFAULT_COMPANY_PILLARS)]
        return ResearchBrief(
            objective=f"Research {company_name.strip()} under the six-pillar investment framework.",
            mode=ResearchMode.COMPANY_EQUITY_RESEARCH,
            entities=[
                ResearchEntity(
                    type="company",
                    name=company_name.strip(),
                    ticker=normalized_ticker,
                )
            ],
            selected_pillars=pillars,
            constraints={
                "framework": "six_pillar_equity_research",
                "ticker": normalized_ticker,
            },
        )

    def create_plan(self, brief: ResearchBrief) -> ResearchPlan:
        """Dispatch to the correct planner for a normalized brief."""
        if brief.mode == ResearchMode.COMPANY_EQUITY_RESEARCH:
            return self.create_company_plan(brief)
        return self.create_open_plan(brief)

    def create_company_plan(self, brief: ResearchBrief) -> ResearchPlan:
        """Create an LLM-led six-pillar company research plan with fallback."""
        fallback = self._create_company_plan_deterministic(brief)
        if not os.getenv("OPENAI_API_KEY", "").strip():
            return fallback
        try:
            model = ChatOpenAI(model=RESEARCH_PLANNER_MODEL, temperature=0)
            structured = model.with_structured_output(ResearchPlanDraft, method="function_calling")
            parsed = structured.invoke(
                [
                    ("system", RESEARCH_PLANNER_SYSTEM),
                    (
                        "human",
                        json.dumps(
                            {
                                "brief": brief.model_dump(mode="json"),
                                "selectedPillars": brief.selected_pillars or list(DEFAULT_COMPANY_PILLARS),
                                "baselinePlan": fallback.model_dump(mode="json"),
                                "pillarEvidenceRequirements": {
                                    pillar: [req.model_dump(mode="json") for req in requirements]
                                    for pillar, requirements in PILLAR_EVIDENCE_REQUIREMENTS.items()
                                },
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ]
            )
            draft = parsed if isinstance(parsed, ResearchPlanDraft) else ResearchPlanDraft.model_validate(parsed)
            return self._plan_from_llm_draft(brief, draft, fallback)
        except Exception:
            return fallback

    def _create_company_plan_deterministic(self, brief: ResearchBrief) -> ResearchPlan:
        """Create the deterministic company plan used as validator/fallback."""
        selected = tuple(brief.selected_pillars or DEFAULT_COMPANY_PILLARS)
        invalid = [pillar for pillar in selected if pillar not in DEFAULT_COMPANY_PILLARS]
        if invalid:
            raise ValueError(f"Unsupported pillars: {', '.join(invalid)}")

        workstreams = [
            ResearchWorkstream(
                id=self._slugify(pillar),
                title=pillar,
                pillar_name=pillar,
                goal=self._pillar_goal(pillar, brief),
                required_evidence=PILLAR_EVIDENCE_REQUIREMENTS[pillar],
                search_focus=PILLAR_SEARCH_FOCUS.get(pillar, []),
                max_iterations=3,
                metadata={"planner": "deterministic_fallback"},
            )
            for pillar in selected
        ]
        return ResearchPlan(
            brief_id=brief.id,
            mode=brief.mode,
            objective=brief.objective,
            entities=brief.entities,
            workstreams=workstreams,
            stop_conditions=StopConditions(
                min_sources_per_workstream=3,
                min_primary_sources_per_core_workstream=1,
                min_evidence_facts_per_workstream=2,
                confidence_threshold=0.7,
                max_plan_iterations=4,
            ),
            metadata={
                "planner": "deterministic_fallback",
                "plannerSource": "deterministic_fallback",
                "selected_pillars": list(selected),
            },
        )

    def _plan_from_llm_draft(self, brief: ResearchBrief, draft: ResearchPlanDraft, fallback: ResearchPlan) -> ResearchPlan:
        """Validate an LLM plan draft and return a harness-ready plan."""
        selected = list(brief.selected_pillars or DEFAULT_COMPANY_PILLARS)
        by_pillar: dict[str, PlannedWorkstream] = {}
        for item in draft.workstreams:
            pillar = item.pillar_name or item.title
            if pillar in selected and pillar not in by_pillar:
                by_pillar[pillar] = item

        workstreams: list[ResearchWorkstream] = []
        for pillar in selected:
            item = by_pillar.get(pillar)
            if item is None:
                base = next(ws for ws in fallback.workstreams if ws.pillar_name == pillar)
                workstreams.append(base)
                continue
            workstreams.append(
                ResearchWorkstream(
                    id=self._slugify(item.id or pillar),
                    title=pillar,
                    pillar_name=pillar,
                    goal=item.goal.strip() or self._pillar_goal(pillar, brief),
                    required_evidence=PILLAR_EVIDENCE_REQUIREMENTS[pillar],
                    search_focus=_dedupe(item.search_focus + PILLAR_SEARCH_FOCUS.get(pillar, []))[:8],
                    open_gaps=item.open_gaps[:6],
                    max_iterations=3,
                    metadata={**item.metadata, "planner": "llm", "instrumentStatus": draft.instrument_status},
                )
            )
        return ResearchPlan(
            brief_id=brief.id,
            mode=brief.mode,
            objective=brief.objective,
            entities=brief.entities,
            workstreams=workstreams,
            stop_conditions=fallback.stop_conditions,
            metadata={
                "planner": "llm",
                "plannerSource": "llm",
                "selected_pillars": selected,
                "plannerReasoning": draft.planner_reasoning,
                "instrumentStatus": draft.instrument_status,
                "deterministicBaselinePlanId": fallback.id,
            },
        )

    def create_open_plan(self, brief: ResearchBrief) -> ResearchPlan:
        """Create a minimal open-ended plan for modes not yet specialized."""
        return ResearchPlan(
            brief_id=brief.id,
            mode=brief.mode,
            objective=brief.objective,
            entities=brief.entities,
            workstreams=[
                ResearchWorkstream(
                    id="explore_research_request",
                    title="Explore research request",
                    goal=brief.objective,
                    required_evidence=[
                        EvidenceRequirement(
                            name="initial source coverage",
                            description="Find enough high-quality sources to understand the request shape.",
                            min_facts=2,
                        )
                    ],
                    search_focus=["authoritative sources", "recent evidence", "source diversity"],
                    metadata={"planner": "open_plan_fallback"},
                )
            ],
            metadata={"planner": "open_plan_fallback"},
        )

    def _pillar_goal(self, pillar: str, brief: ResearchBrief) -> str:
        """Create a human-readable goal for one company research pillar."""
        entity = brief.entities[0] if brief.entities else ResearchEntity(name="the target company")
        ticker_hint = f" ({entity.ticker})" if entity.ticker else ""
        return f"Assess {entity.name}{ticker_hint} for the {pillar} pillar with source-grounded evidence."

    def _slugify(self, value: str) -> str:
        """Convert display titles into stable workstream ids."""
        slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        return slug or "workstream"
