"""Typed state models used by the research agent harness.

The harness exists to make agent state explicit. These models are intentionally
broader than the current six-pillar stock pipeline so the same controller can
later support industry scans, company comparisons, and personalized research.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


DEFAULT_COMPANY_PILLARS: tuple[str, ...] = (
    "Macro & Industry",
    "Economic Moat",
    "Financial Engine",
    "Management & Capital Allocation",
    "Valuation",
    "Technical Analysis",
)


def utc_now_iso() -> str:
    """Return an ISO timestamp for persisted harness events."""
    return datetime.now(timezone.utc).isoformat()


class ResearchMode(StrEnum):
    """High-level research mode selected by the router/planner."""

    COMPANY_EQUITY_RESEARCH = "company_equity_research"
    INDUSTRY_OPPORTUNITY_SCAN = "industry_opportunity_scan"
    COMPANY_COMPARISON = "company_comparison"
    PORTFOLIO_PERSONALIZED_RESEARCH = "portfolio_personalized_research"
    OPEN_ENDED_RESEARCH = "open_ended_research"


class ResearchPlanStatus(StrEnum):
    """Lifecycle status for the full research plan."""

    PENDING = "pending"
    RUNNING = "running"
    NEEDS_RETRY = "needs_retry"
    NEEDS_HUMAN_INPUT = "needs_human_input"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkstreamStatus(StrEnum):
    """Lifecycle status for one research workstream."""

    PENDING = "pending"
    RUNNING = "running"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"
    READY_FOR_ANALYSIS = "ready_for_analysis"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class ResearchEntity(BaseModel):
    """An entity the harness is researching, such as a company or industry."""

    type: Literal["company", "ticker", "industry", "portfolio", "market", "other"] = "company"
    name: str = Field(min_length=1, description="Human-readable entity name.")
    ticker: str = Field(default="", description="Ticker symbol when the entity is a listed company.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional identifiers such as exchange, country, sector, or source ids.",
    )

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        """Store tickers consistently for downstream artifact paths and search tools."""
        return value.strip().upper()


class EvidenceRequirement(BaseModel):
    """A concrete evidence target that tells the harness what 'enough' means."""

    name: str = Field(min_length=1, description="Evidence target name, e.g. 'recent revenue'.")
    description: str = Field(default="", description="Why this evidence matters for the workstream.")
    required: bool = Field(default=True, description="Whether the gate should treat this as mandatory.")
    min_facts: int = Field(default=1, ge=0, description="Minimum extracted facts expected for this target.")
    preferred_source_types: list[str] = Field(
        default_factory=list,
        description="Preferred source types, e.g. regulatory, investor_relations, financial_data.",
    )


class ResearchWorkstream(BaseModel):
    """A unit of research the controller can plan, execute, grade, and retry.

    For phase one, each six-pillar pillar becomes one workstream. For later
    phases, a workstream can represent an industry map, candidate screen,
    company comparison, or user-specific suitability check.
    """

    id: str = Field(min_length=1, description="Stable id used in traces and artifacts.")
    title: str = Field(min_length=1, description="Display title for progress and reports.")
    goal: str = Field(min_length=1, description="The question this workstream must answer.")
    status: WorkstreamStatus = WorkstreamStatus.PENDING
    pillar_name: str = Field(default="", description="Six-pillar name when this maps to a pillar.")
    required_evidence: list[EvidenceRequirement] = Field(default_factory=list)
    search_focus: list[str] = Field(
        default_factory=list,
        description="Search angles or source families the planner wants the tools to prioritize.",
    )
    open_gaps: list[str] = Field(
        default_factory=list,
        description="Known evidence or analysis gaps that should drive follow-up actions.",
    )
    completed_steps: list[str] = Field(
        default_factory=list,
        description="Tool or workflow step names already completed for this workstream.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    max_iterations: int = Field(default=2, ge=1)
    iteration_count: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Agentic loop fields — set by planner and updated by QueryRefinementTool
    tool_assignment: Literal["grounded_search", "web_search", "legacy_pipeline"] = Field(
        default="grounded_search",
        description=(
            "Which primary search tool the agent should use for this workstream. "
            "The planner sets this based on API key availability. "
            "The agent loop can override it when a tool fails."
        ),
    )
    query_hints: list[str] = Field(
        default_factory=list,
        description=(
            "Targeted search queries generated by QueryRefinementTool when the "
            "previous iteration returned thin evidence. Injected into the next "
            "grounded_search or web_search call."
        ),
    )

    def mark_step_completed(self, step_name: str) -> None:
        """Record a completed step without duplicating the step history."""
        if step_name not in self.completed_steps:
            self.completed_steps.append(step_name)


class StopConditions(BaseModel):
    """Boundaries that prevent research loops from running without end."""

    min_sources_per_workstream: int = Field(default=3, ge=0)
    min_primary_sources_per_core_workstream: int = Field(default=1, ge=0)
    min_evidence_facts_per_workstream: int = Field(default=2, ge=0)
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    max_plan_iterations: int = Field(default=2, ge=1)


class ResearchBrief(BaseModel):
    """Normalized user intent that seeds planning and context building."""

    id: str = Field(default_factory=lambda: f"brief_{uuid4().hex}")
    objective: str = Field(min_length=1)
    mode: ResearchMode
    entities: list[ResearchEntity] = Field(default_factory=list)
    selected_pillars: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="Research limits such as geography, time horizon, or user-provided constraints.",
    )
    user_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional user/portfolio context for personalized research modes.",
    )
    created_at: str = Field(default_factory=utc_now_iso)


class ResearchObservation(BaseModel):
    """A compact record of what happened after a tool or workflow ran."""

    workstream_id: str = Field(default="")
    stage: str = Field(min_length=1)
    summary: str = Field(default="")
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class QualityGateResult(BaseModel):
    """Structured output from a gate that grades plan or workstream quality."""

    passed: bool
    gate_name: str = Field(min_length=1)
    summary: str = Field(default="")
    recommended_action: Literal["continue", "retry", "branch", "ask_user", "stop"] = "continue"
    target_workstream_id: str = Field(default="")
    gaps: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class GroundedSource(BaseModel):
    """A source returned by Gemini grounding metadata."""

    source_id: str = Field(min_length=1)
    title: str = Field(default="")
    url: str = Field(default="")
    source_kind: str = Field(default="grounded_web")


class GroundedCitationSupport(BaseModel):
    """A grounded answer segment and the source ids that support it."""

    text: str = Field(default="")
    source_ids: list[str] = Field(default_factory=list)


class GroundedResearchResult(BaseModel):
    """Structured result from a Gemini grounded workstream research call."""

    workstream_id: str
    pillar_name: str = Field(default="")
    query: str = Field(default="")
    answer: str = Field(default="")
    web_search_queries: list[str] = Field(default_factory=list)
    sources: list[GroundedSource] = Field(default_factory=list)
    citation_supports: list[GroundedCitationSupport] = Field(default_factory=list)
    evidence_candidates: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["success", "unavailable", "failed"] = "success"
    error_message: str = Field(default="")


class PrimaryDocument(BaseModel):
    """Canonical primary document candidate used by the document pipeline."""

    document_id: str
    title: str = Field(default="")
    url: str = Field(default="")
    document_type: str = Field(default="html")
    source_kind: str = Field(default="general_web")
    discovery_type: str = Field(default="")
    is_primary_source: bool = False
    score: int = 0
    local_path: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedTable(BaseModel):
    """A normalized table extracted from a parsed document."""

    document_id: str
    table_id: str
    title: str = Field(default="")
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    statement_type: str = Field(default="unknown")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ParsedDocument(BaseModel):
    """Parsed text and table content from one primary document."""

    document_id: str
    title: str = Field(default="")
    url: str = Field(default="")
    document_type: str = Field(default="html")
    text: str = Field(default="")
    text_chunks: list[str] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    parser: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentEvidenceFact(BaseModel):
    """Evidence fact derived from a primary document or extracted table."""

    pillar_name: str
    signal_name: str
    source_title: str = Field(default="")
    document_id: str = Field(default="")
    metric_name: str = Field(default="")
    metric_value: str = Field(default="")
    period: str = Field(default="")
    excerpt: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_kind: str = Field(default="primary_document")


class ResearchPlan(BaseModel):
    """Mutable plan that the harness executes and updates over time.

    The plan is intentionally mode-agnostic. A deterministic planner, an LLM
    planner, or a human-authored plan can all produce this same shape.
    """

    id: str = Field(default_factory=lambda: f"plan_{uuid4().hex}")
    brief_id: str
    mode: ResearchMode
    objective: str = Field(min_length=1)
    entities: list[ResearchEntity] = Field(default_factory=list)
    workstreams: list[ResearchWorkstream] = Field(default_factory=list)
    status: ResearchPlanStatus = ResearchPlanStatus.PENDING
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
    observations: list[ResearchObservation] = Field(default_factory=list)
    gate_results: list[QualityGateResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    def add_observation(self, observation: ResearchObservation) -> None:
        """Attach an observation and update the plan timestamp."""
        self.observations.append(observation)
        self.updated_at = utc_now_iso()

    def add_gate_result(self, gate_result: QualityGateResult) -> None:
        """Attach a quality gate result and reflect retry status on the plan."""
        self.gate_results.append(gate_result)
        if not gate_result.passed and gate_result.recommended_action == "retry":
            self.status = ResearchPlanStatus.NEEDS_RETRY
        self.updated_at = utc_now_iso()

    def workstream_by_id(self, workstream_id: str) -> ResearchWorkstream | None:
        """Find a workstream by its stable id."""
        return next((item for item in self.workstreams if item.id == workstream_id), None)
