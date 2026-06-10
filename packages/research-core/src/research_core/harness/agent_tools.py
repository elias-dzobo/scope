"""Atomic research tools for the agentic research loop.

Each tool is a callable unit with a well-defined input (ToolContext) and
output (ToolResult). The ResearchAgentLoop in runner.py selects and chains
tools based on evidence quality — no hardcoded pipeline sequence.

Usage
-----
The agent loop picks tools by name from the ToolRegistry:

    result = registry.get("grounded_search").run(ctx)
    if result.quality_score < 0.4:
        refinement = registry.get("query_refinement").run(ctx, gaps=...)
        result = registry.get("grounded_search").run(ctx)   # retry with hints

Tools communicate via ToolContext (shared mutable state) and ToolResult
(output envelope). Suggested next tool is a hint — the loop decides.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

from research_core.harness.models import (
    GroundedResearchResult,
    ParsedDocument,
    PrimaryDocument,
    ResearchBrief,
    ResearchWorkstream,
)
from research_core.utils.logger import get_logger

if TYPE_CHECKING:
    from research_core.harness.tools import ResearchToolFacade
    from research_core.harness.gates import ResearchQualityGates

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared context and result types
# ---------------------------------------------------------------------------


@dataclass
class ToolContext:
    """Shared context threaded through every tool invocation in one workstream.

    Tools can read and write the accumulated state fields (grounded_results,
    documents, etc.) so the loop has a single source of truth.
    """

    brief: ResearchBrief
    workstream: ResearchWorkstream
    ticker: str
    company_name: str
    iteration: int = 0
    # Accumulated research state — tools append here as they run
    grounded_results: list[GroundedResearchResult] = field(default_factory=list)
    documents: list[PrimaryDocument] = field(default_factory=list)
    parsed_documents: dict[str, ParsedDocument] = field(default_factory=dict)
    evidence_by_pillar: dict[str, list[dict]] = field(default_factory=dict)
    artifact_records: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolResult:
    """Standard output envelope for every tool call.

    ``quality_score`` is the key signal the loop uses to decide whether to
    accept the result or trigger a refinement cycle. 0.0 = useless, 1.0 = excellent.
    """

    tool_name: str
    success: bool
    output: Any = None
    quality_score: float = 0.0
    reasoning: str = ""
    suggested_next_tool: str | None = None
    error: str = ""


@runtime_checkable
class AgentTool(Protocol):
    """Protocol every atomic tool must satisfy."""

    name: str
    description: str

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        ...


# ---------------------------------------------------------------------------
# Concrete tools
# ---------------------------------------------------------------------------


class QueryGenerationTool:
    """Generate a search prompt for a workstream, injecting any query hints."""

    name = "query_generation"
    description = (
        "Build a Gemini grounded-search prompt from the workstream goal and "
        "evidence requirements. Injects query_hints when the workstream has them."
    )

    def __init__(self, facade: "ResearchToolFacade") -> None:
        self._facade = facade

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        from research_core.harness.grounding import build_grounded_prompt

        try:
            prompt = build_grounded_prompt(ctx.brief, ctx.workstream)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output={"prompt": prompt, "query_hints": ctx.workstream.query_hints},
                quality_score=1.0,
                reasoning="Prompt built from workstream goal, evidence requirements, and hints.",
            )
        except Exception as exc:
            logger.warning("QueryGenerationTool failed: %s", exc)
            return ToolResult(tool_name=self.name, success=False, error=str(exc))


class GroundedSearchTool:
    """Run Gemini grounded web search for a workstream."""

    name = "grounded_search"
    description = (
        "Search using Gemini grounded web search. Returns a grounded answer with "
        "citations and structured evidence candidates. Preferred over web_search "
        "when GOOGLE_API_KEY is available."
    )

    def __init__(self, facade: "ResearchToolFacade") -> None:
        self._facade = facade

    def run(self, ctx: ToolContext, prompt: str | None = None, **kwargs: Any) -> ToolResult:
        from research_core.harness.grounding import (
            build_grounded_prompt,
            call_gemini_grounded,
            parse_grounded_response,
        )

        try:
            if prompt is None:
                prompt = build_grounded_prompt(ctx.brief, ctx.workstream)
            payload = call_gemini_grounded(prompt)
            result = parse_grounded_response(payload, ctx.workstream, brief=ctx.brief)

            evidence_count = len(result.evidence_candidates)
            source_count = len(result.sources)
            # Quality: 5 evidence candidates + 5 sources = perfect score
            quality = min(1.0, (evidence_count / 5.0 * 0.6) + (source_count / 5.0 * 0.4))
            suggested = "query_refinement" if quality < 0.4 else None

            return ToolResult(
                tool_name=self.name,
                success=True,
                output=result,
                quality_score=quality,
                reasoning=f"{evidence_count} evidence candidates, {source_count} sources",
                suggested_next_tool=suggested,
            )
        except Exception as exc:
            logger.warning("GroundedSearchTool failed: %s", exc)
            return ToolResult(
                tool_name=self.name,
                success=False,
                output=GroundedResearchResult(
                    workstream_id=ctx.workstream.id,
                    pillar_name=ctx.workstream.pillar_name or ctx.workstream.title,
                    status="failed",
                    error_message=str(exc),
                ),
                error=str(exc),
                suggested_next_tool="web_search",
            )


class WebSearchTool:
    """Search the web directly using the provider search tool."""

    name = "web_search"
    description = (
        "Run targeted web search queries via the search provider and return raw results. "
        "Use as fallback when Gemini grounding is unavailable, or to supplement thin grounding results."
    )

    def __init__(self, facade: "ResearchToolFacade") -> None:
        self._facade = facade

    def run(self, ctx: ToolContext, queries: list[str] | None = None, **kwargs: Any) -> ToolResult:
        from provider_integrations.tools.main import search_tool

        try:
            if queries is None:
                queries = ctx.workstream.query_hints or [
                    f"{ctx.company_name} {ctx.workstream.pillar_name or ctx.workstream.title} {ctx.ticker}"
                ]
            all_results: list[dict] = []
            for query in queries[:5]:  # cap to avoid runaway provider spend
                results = search_tool({"query": query})
                all_results.extend(list(results))

            quality = min(1.0, len(all_results) / 10.0)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=all_results,
                quality_score=quality,
                reasoning=f"{len(all_results)} results from {len(queries)} queries",
            )
        except Exception as exc:
            logger.warning("WebSearchTool failed: %s", exc)
            return ToolResult(tool_name=self.name, success=False, error=str(exc))


class DocumentDiscoveryTool:
    """Discover, classify, and rank primary documents from grounding sources."""

    name = "document_discovery"
    description = (
        "Classify and rank document candidates into a prioritized PrimaryDocument list. "
        "Filters out unrelated SEC filings and boosts annual reports, 10-Ks, and IR pages."
    )

    def __init__(self, facade: "ResearchToolFacade") -> None:
        self._facade = facade

    def run(
        self,
        ctx: ToolContext,
        grounded_results: list[GroundedResearchResult] | None = None,
        max_documents: int = 8,
        pre_fetched_candidates: list[dict] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            documents = self._facade.discover_primary_documents(
                ctx.company_name,
                ctx.ticker,
                grounded_results=grounded_results or ctx.grounded_results,
                max_documents=max_documents,
                pre_fetched_candidates=pre_fetched_candidates,
            )
            primary_count = sum(1 for d in documents if d.is_primary_source)
            quality = min(1.0, 0.3 + (primary_count / 3.0 * 0.7))
            suggested = "query_refinement" if primary_count == 0 else None
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=documents,
                quality_score=quality,
                reasoning=f"{len(documents)} docs found, {primary_count} primary sources",
                suggested_next_tool=suggested,
            )
        except Exception as exc:
            logger.warning("DocumentDiscoveryTool failed: %s", exc)
            return ToolResult(tool_name=self.name, success=False, error=str(exc), output=[])


class DocumentFetchTool:
    """Fetch one primary document and persist raw content under artifacts."""

    name = "document_fetch"
    description = (
        "Download a primary document (PDF or HTML) and return raw content for parsing. "
        "Persists the raw bytes to the artifact store."
    )

    def __init__(self, facade: "ResearchToolFacade") -> None:
        self._facade = facade

    def run(self, ctx: ToolContext, document: PrimaryDocument, **kwargs: Any) -> ToolResult:
        try:
            fetched, raw_bytes, html_text = self._facade.fetch_document(ctx.ticker, document)
            # PDFs are richer than HTML
            quality = 1.0 if fetched.document_type == "pdf" else 0.7
            return ToolResult(
                tool_name=self.name,
                success=True,
                output={"document": fetched, "raw_bytes": raw_bytes, "html_text": html_text},
                quality_score=quality,
                reasoning=f"Fetched {fetched.document_type}: {fetched.title}",
            )
        except Exception as exc:
            document.metadata["fetch_error"] = str(exc)
            logger.warning("DocumentFetchTool failed for %s: %s", document.url, exc)
            return ToolResult(tool_name=self.name, success=False, error=str(exc))


class DocumentParseTool:
    """Parse a fetched document into structured text and financial tables."""

    name = "document_parse"
    description = (
        "Parse a raw document into text chunks and financial tables. "
        "Persists parsed output to the artifact store and returns a ParsedDocument."
    )

    def __init__(self, facade: "ResearchToolFacade") -> None:
        self._facade = facade

    def run(
        self,
        ctx: ToolContext,
        document: PrimaryDocument,
        raw_bytes: bytes | None = None,
        html_text: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        try:
            parsed = self._facade.parse_document(
                ctx.ticker,
                document,
                raw_bytes=raw_bytes,
                html_text=html_text,
            )
            table_count = len(parsed.tables)
            text_len = len(parsed.text)
            quality = min(1.0, (text_len / 5000.0 * 0.5) + (table_count / 5.0 * 0.5))
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=parsed,
                quality_score=quality,
                reasoning=f"Parsed {text_len} chars, {table_count} tables",
            )
        except Exception as exc:
            logger.warning("DocumentParseTool failed: %s", exc)
            return ToolResult(tool_name=self.name, success=False, error=str(exc))


class EvidenceExtractionTool:
    """Extract structured evidence facts from a parsed document for one pillar."""

    name = "evidence_extraction"
    description = (
        "Extract evidence facts (metrics, excerpts, signals) from a parsed document "
        "for a specific research pillar. Returns a list of fact dicts."
    )

    def __init__(self, facade: "ResearchToolFacade") -> None:
        self._facade = facade

    def run(
        self,
        ctx: ToolContext,
        pillar: str,
        document: PrimaryDocument,
        parsed: ParsedDocument,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            facts = self._facade.extract_document_evidence(pillar, document, parsed)
            quality = min(1.0, len(facts) / 5.0)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=facts,
                quality_score=quality,
                reasoning=f"Extracted {len(facts)} facts for {pillar}",
            )
        except Exception as exc:
            logger.warning("EvidenceExtractionTool failed: %s", exc)
            return ToolResult(
                tool_name=self.name,
                success=False,
                output=[],
                error=str(exc),
            )


class QueryRefinementTool:
    """The core agentic tool: derive better queries from evidence gaps.

    When grounded search or document discovery returns thin evidence, this tool
    analyzes the gaps and updates the workstream's query_hints so the next
    iteration targets the missing data more precisely.

    No LLM call required — gap patterns drive deterministic hint generation.
    The loop can optionally inject an LLM call here in a future phase.
    """

    name = "query_refinement"
    description = (
        "Analyze evidence gaps and update the workstream's query_hints for the next "
        "search iteration. Call this when evidence is thin or quality gates flag gaps. "
        "Deterministic — no extra LLM token cost."
    )

    def __init__(self, facade: "ResearchToolFacade") -> None:
        self._facade = facade

    def run(
        self,
        ctx: ToolContext,
        gaps: list[str] | None = None,
        current_evidence_count: int = 0,
        **kwargs: Any,
    ) -> ToolResult:
        effective_gaps = gaps or ctx.workstream.open_gaps or []
        entity_name = ctx.brief.entities[0].name if ctx.brief.entities else ctx.company_name
        ticker = ctx.brief.entities[0].ticker if ctx.brief.entities else ctx.ticker
        pillar = ctx.workstream.pillar_name or ctx.workstream.title

        hints = derive_query_hints(
            company_name=entity_name,
            ticker=ticker,
            pillar=pillar,
            gaps=effective_gaps,
            search_focus=ctx.workstream.search_focus,
            iteration=ctx.workstream.iteration_count,
        )

        # Mutate workstream in-place so next tool call sees the updated hints
        ctx.workstream.query_hints = hints
        ctx.workstream.open_gaps = effective_gaps
        ctx.workstream.iteration_count += 1

        logger.info(
            "QueryRefinementTool: pillar=%s hints=%s gaps=%s",
            pillar,
            hints,
            effective_gaps[:2],
        )

        return ToolResult(
            tool_name=self.name,
            success=True,
            output=hints,
            quality_score=1.0,
            reasoning=f"Generated {len(hints)} targeted hints from {len(effective_gaps)} gaps",
            suggested_next_tool="grounded_search",
        )


class EvidenceQualityTool:
    """Run the quality gate on assembled evidence and return gaps."""

    name = "evidence_quality"
    description = (
        "Evaluate assembled evidence against the research plan's quality thresholds. "
        "Returns gate pass/fail and a list of gap descriptions the loop can act on."
    )

    def __init__(self, facade: "ResearchToolFacade", gates: "ResearchQualityGates") -> None:
        self._facade = facade
        self._gates = gates

    def run(
        self,
        ctx: ToolContext,
        plan: Any,
        evidence_by_pillar: dict | None = None,
        sources_by_pillar: dict | None = None,
        documents: list | None = None,
        document_tables: list | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            ev = evidence_by_pillar if evidence_by_pillar is not None else ctx.evidence_by_pillar
            gate_result = self._gates.evaluate_company_research(
                plan,
                {
                    "evidence_by_pillar": ev,
                    "sources_by_pillar": sources_by_pillar or {},
                    "documents": documents or [],
                    "document_tables": document_tables or [],
                },
            )
            plan.add_gate_result(gate_result)
            quality = 1.0 if gate_result.passed else max(0.1, 1.0 - len(gate_result.gaps) * 0.15)
            suggested = None if gate_result.passed else "query_refinement"
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=gate_result,
                quality_score=quality,
                reasoning=(
                    "Gate passed"
                    if gate_result.passed
                    else f"Gate failed: {gate_result.gaps[:2]}"
                ),
                suggested_next_tool=suggested,
            )
        except Exception as exc:
            logger.warning("EvidenceQualityTool failed: %s", exc)
            return ToolResult(tool_name=self.name, success=False, error=str(exc))


class PillarScoringTool:
    """Assess all pillars and build the final scorecard."""

    name = "pillar_scoring"
    description = (
        "Run deterministic pillar signal assessment and build the investment scorecard. "
        "Returns pillar_assessments and scorecard dicts."
    )

    def __init__(self, facade: "ResearchToolFacade") -> None:
        self._facade = facade

    def run(
        self,
        ctx: ToolContext,
        company_name: str | None = None,
        evidence_by_pillar: dict | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            ev = evidence_by_pillar if evidence_by_pillar is not None else ctx.evidence_by_pillar
            name = company_name or ctx.company_name
            # Resolve asset class from the brief so scoring uses the right signals/weights.
            _ac = ctx.brief.entities[0].asset_class if ctx.brief.entities else ""
            pillar_assessments = self._facade.assess_pillars(ev, asset_class=_ac or None)
            scorecard = self._facade.build_scorecard(name, ctx.ticker, pillar_assessments, ev, asset_class=_ac or None)
            overall = scorecard.get("overall_score", 0)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output={"pillar_assessments": pillar_assessments, "scorecard": scorecard},
                quality_score=min(1.0, overall / 10.0),
                reasoning=f"Score: {overall}, recommendation: {scorecard.get('recommendation', 'N/A')}",
            )
        except Exception as exc:
            logger.warning("PillarScoringTool failed: %s", exc)
            return ToolResult(tool_name=self.name, success=False, error=str(exc))


class SynthesisTool:
    """Generate the final investment memo — fails gracefully without killing the run."""

    name = "synthesis"
    description = (
        "Generate the investor-facing investment memo from scored evidence. "
        "Returns partial result on failure instead of raising — the service layer "
        "detects synthesis_failed=True and uses completed_partial status."
    )

    def __init__(self, facade: "ResearchToolFacade") -> None:
        self._facade = facade

    def run(
        self,
        ctx: ToolContext,
        summary: dict,
        user_financial_profile: dict | None = None,
        user_risk_profile: dict | None = None,
        investor_context: dict | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        from research_core.synthesis.final_synthesis import FinalSynthesisGenerator

        try:
            gen = FinalSynthesisGenerator()
            # Reuse static helper from the runner for consistent pillar ordering
            from research_core.harness.runner import _pillars_order_for_synthesis

            pillars_order = _pillars_order_for_synthesis(summary)
            final = gen.generate(
                company_name=str(summary.get("stock_name", "")),
                ticker=str(summary.get("ticker", "")),
                scorecard=dict(summary.get("scorecard") or {}),
                pillar_assessments=dict(summary.get("pillar_assessments") or {}),
                evidence_by_pillar=dict(summary.get("evidence_by_pillar") or {}),
                sources_by_pillar=dict(summary.get("sources_by_pillar") or {}),
                pillars_order=pillars_order,
                user_financial_profile=user_financial_profile,
                user_risk_profile=user_risk_profile,
                investor_context=investor_context,
            )
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=final,
                quality_score=1.0,
                reasoning="Investment memo generated.",
            )
        except Exception as exc:
            logger.error("SynthesisTool failed: %s", exc)
            return ToolResult(
                tool_name=self.name,
                success=False,
                output=None,
                error=str(exc),
                reasoning="Synthesis failed — partial result is still available.",
            )


# ---------------------------------------------------------------------------
# Query refinement helper (shared by QueryRefinementTool and runner replan)
# ---------------------------------------------------------------------------


def derive_query_hints(
    company_name: str,
    ticker: str,
    pillar: str,
    gaps: list[str],
    search_focus: list[str],
    iteration: int = 0,
) -> list[str]:
    """Generate targeted search hints from known evidence gaps.

    Deterministic — no LLM call. Gap patterns drive hint selection.
    Each iteration escalates to more specific queries so the loop
    doesn't repeat the same search that already failed.
    """
    combined = " ".join(gaps).lower()
    pillar_lower = pillar.lower()
    hints: list[str] = []

    if "thin_content" in combined or "no aligned facts" in combined or "insufficient" in combined:
        hints.append(f"{company_name} {pillar_lower} analysis {ticker}")

    if "no primary source" in combined or "primary" in combined:
        hints.append(f"{company_name} annual report investor relations {ticker}")

    if "stale" in combined:
        hints.append(f"{company_name} latest results 2025 {ticker}")

    if "p/e" in combined or "multiple" in combined or "valuation" in pillar_lower:
        hints.append(f"{ticker} trailing P/E forward multiple analyst consensus")

    if "revenue" in combined or "financial engine" in pillar_lower:
        hints.append(f"{company_name} revenue growth earnings {ticker} 2024 2025")

    if "moat" in combined or "competitive" in combined or "economic moat" in pillar_lower:
        hints.append(f"{company_name} competitive advantage market share {ticker}")

    if "macro" in combined or "industry" in combined or "macro" in pillar_lower:
        hints.append(f"{company_name} industry outlook sector trends {ticker}")

    if "management" in combined or "capital allocation" in combined:
        hints.append(
            f"{company_name} management capital allocation buybacks dividends {ticker}"
        )

    if "technical" in combined or "technical analysis" in pillar_lower:
        hints.append(f"{ticker} technical analysis moving averages momentum RSI")

    # Use search_focus to fill remaining slots
    for focus in search_focus:
        if len(hints) >= 3:
            break
        candidate = f"{company_name} {focus} {ticker}"
        if candidate not in hints:
            hints.append(candidate)

    # Escalate on subsequent iterations: add year-specific and filing queries
    if iteration >= 1 and len(hints) < 3:
        hints.append(f"{company_name} {ticker} 10-K annual filing SEC 2024")
    if iteration >= 2 and len(hints) < 3:
        hints.append(f"{ticker} earnings call transcript Q4 2024 analyst")

    # Guaranteed fallback
    if not hints:
        hints.append(f"{company_name} {ticker} {pillar_lower} research analysis")

    return hints[:3]
