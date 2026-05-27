"""Research Agent tool implementations.

The agent gathers evidence with four tools:

  search_web        Gemini-grounded search — returns sources + excerpts
  store_evidence    Write one evidence item to the run's working set
  evaluate_coverage Check coverage counts per pillar to identify gaps
  finalize_research [terminal] Mark research complete, trigger scoring

Architectural split
-------------------
The agent's job is GATHERING.  Scoring and synthesis happen AFTER the
harness finishes, in ResearchAgent.run(), using the working set that these
tools accumulated in context.state.

This keeps the tools fast and the agent loop focused.  The finalize_research
tool is terminal — it signals completion and the harness exits.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

from agent_core.tool import AgentTool
from research_core.utils.logger import get_logger

logger = get_logger(__name__)

# Max evidence items per (pillar, signal) pair — matches grounding.py cap
_MAX_PER_SIGNAL = 6


# ---------------------------------------------------------------------------
# search_web
# ---------------------------------------------------------------------------


class SearchWebInput(BaseModel):
    query: str = Field(description="Specific search query including the company name and ticker.")
    pillar: str = Field(description="Research pillar this search serves (e.g. 'Financial Engine').")
    focus: str = Field(
        default="",
        description="Optional focus hint, e.g. 'recent earnings', 'annual report', 'SEC filing'.",
    )


class SearchWebTool(AgentTool):
    name: str = "search_web"
    description: str = (
        "Run a Gemini-grounded web search for the target company. "
        "Include the company name and ticker in every query. "
        "Returns sources and excerpts — use store_evidence to save the relevant ones."
    )
    args_schema: type[BaseModel] = SearchWebInput
    max_calls_per_run: int = 24  # 4 searches × 6 pillars

    def _run(self, query: str, pillar: str, focus: str = "") -> str:  # type: ignore[override]
        from research_core.harness.grounding import call_gemini_grounded, parse_grounded_response
        from research_core.harness.models import ResearchBrief, ResearchEntity, ResearchMode, ResearchWorkstream

        full_query = f"{query} {focus}".strip()
        logger.debug("search_web | pillar=%s query=%s", pillar, full_query[:80])

        # Build a minimal workstream so parse_grounded_response works correctly
        workstream = ResearchWorkstream(
            id=f"agent_{pillar.replace(' ', '_').lower()}",
            title=pillar,
            pillar_name=pillar,
            goal=full_query,
            required_evidence=[],
        )
        brief = ResearchBrief(
            objective=full_query,
            mode=ResearchMode.COMPANY_EQUITY_RESEARCH,
            entities=[ResearchEntity(type="ticker", name=self.ctx.metadata.get("company_name", ""), ticker=self.ctx.metadata.get("ticker", ""))],
        )

        try:
            raw = call_gemini_grounded(full_query)
            result = parse_grounded_response(raw, workstream, brief=brief)  # type: ignore[arg-type]
        except Exception as exc:
            logger.error("search_web failed | query=%s error=%s", full_query[:60], exc)
            return f"Search failed: {exc}"

        if result.status == "unavailable":
            return f"Search unavailable: {result.error_message}"
        if result.status != "success":
            return f"Search returned no results for: {full_query}"

        # Format output for the agent — concise, token-efficient
        lines = [f'SEARCH: "{full_query}"', f"Sources: {len(result.sources)}  Evidence candidates: {len(result.evidence_candidates)}"]

        for i, source in enumerate(result.sources[:8], 1):
            lines.append(f"\n[{i}] {source.title or 'Untitled'} — {source.url[:80]}")

        if result.evidence_candidates:
            lines.append("\nTop evidence candidates:")
            for item in result.evidence_candidates[:6]:
                excerpt = str(item.get("excerpt") or item.get("text") or "")[:200]
                signal = item.get("signal_name", "")
                conf = item.get("confidence", 0.0)
                src_title = item.get("source_title", "")
                lines.append(f"  [{signal}] (conf={conf:.2f}) [{src_title[:40]}] {excerpt}")
        else:
            if result.answer:
                lines.append(f"\nAnswer text: {result.answer[:400]}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# store_evidence
# ---------------------------------------------------------------------------


class StoreEvidenceInput(BaseModel):
    pillar: str = Field(description="Research pillar, e.g. 'Financial Engine'.")
    signal: str = Field(description="Signal within the pillar, e.g. 'Profitability'.")
    excerpt: str = Field(description="The specific text excerpt (max 350 chars). Must contain concrete data.")
    source_url: str = Field(default="", description="URL of the source.")
    source_title: str = Field(default="", description="Title of the source.")
    confidence: float = Field(default=0.4, ge=0.0, le=1.0, description="Evidence confidence score (0–1).")


class StoreEvidenceTool(AgentTool):
    name: str = "store_evidence"
    description: str = (
        "Store one piece of evidence into the research working set. "
        "Only store excerpts with concrete facts, numbers, or sourced claims. "
        "Each call stores one item — call multiple times for multiple items."
    )
    args_schema: type[BaseModel] = StoreEvidenceInput
    max_calls_per_run: int = 60  # generous cap — agent curates quality over quantity

    def _run(  # type: ignore[override]
        self,
        pillar: str,
        signal: str,
        excerpt: str,
        source_url: str = "",
        source_title: str = "",
        confidence: float = 0.4,
    ) -> str:
        state = self.ctx.state
        ticker = self.ctx.metadata.get("ticker", "")

        # Initialise working set on first call
        if "evidence_by_pillar" not in state:
            state["evidence_by_pillar"] = defaultdict(list)
        if "sources_by_pillar" not in state:
            state["sources_by_pillar"] = defaultdict(list)
        if "_signal_counts" not in state:
            state["_signal_counts"] = defaultdict(int)

        ebp = state["evidence_by_pillar"]
        sbp = state["sources_by_pillar"]
        counts = state["_signal_counts"]

        signal_key = f"{pillar}::{signal}"

        # Per-signal cap
        if counts[signal_key] >= _MAX_PER_SIGNAL:
            return f"Signal '{signal}' in '{pillar}' already has {_MAX_PER_SIGNAL} items — skipped."

        # Deduplicate by excerpt fingerprint
        fingerprint = excerpt.strip().lower()[:120]
        existing_fingerprints = state.setdefault("_evidence_fingerprints", set())
        if fingerprint in existing_fingerprints:
            return "Duplicate evidence — skipped."
        existing_fingerprints.add(fingerprint)

        item = {
            "pillar_name": pillar,
            "signal_name": signal,
            "excerpt": excerpt[:350],
            "source_title": source_title[:120],
            "source_url": source_url,
            "confidence": round(float(confidence), 3),
            "source_kind": "agent_web_search",
            "metric_name": "",
            "metric_value": "",
            "period": "",
        }
        ebp[pillar].append(item)
        counts[signal_key] += 1

        # Track sources
        if source_url:
            existing_urls = {s.get("url") for s in sbp.get(pillar, [])}
            if source_url not in existing_urls:
                sbp[pillar].append({"title": source_title, "url": source_url, "pillar": pillar})

        total = sum(len(v) for v in ebp.values())
        logger.debug("store_evidence | pillar=%s signal=%s total=%d", pillar, signal, total)
        return f"Stored. {pillar} / {signal}: {counts[signal_key]}/{_MAX_PER_SIGNAL}. Total evidence: {total}."


# ---------------------------------------------------------------------------
# evaluate_coverage
# ---------------------------------------------------------------------------


class EvaluateCoverageInput(BaseModel):
    selected_pillars: list[str] = Field(
        description="List of required pillars to evaluate coverage for."
    )


class EvaluateCoverageTool(AgentTool):
    name: str = "evaluate_coverage"
    description: str = (
        "Check how much evidence has been gathered per pillar. "
        "Call this after covering all pillars to identify gaps before finalizing."
    )
    args_schema: type[BaseModel] = EvaluateCoverageInput
    max_calls_per_run: int = 4

    def _run(self, selected_pillars: list[str]) -> str:  # type: ignore[override]
        ebp = self.ctx.state.get("evidence_by_pillar") or {}
        lines = ["COVERAGE REPORT:"]
        weak_pillars = []

        for pillar in selected_pillars:
            items = ebp.get(pillar) or []
            count = len(items)
            strength = "✓ strong" if count >= 3 else ("△ thin" if count >= 1 else "✗ empty")
            lines.append(f"  {pillar}: {count} items — {strength}")
            if count < 2:
                weak_pillars.append(pillar)

        total = sum(len(v) for v in ebp.values())
        lines.append(f"\nTotal evidence items: {total}")
        if weak_pillars:
            lines.append(f"Pillars needing more research: {', '.join(weak_pillars)}")
        else:
            lines.append("All required pillars have sufficient coverage. Ready to finalize.")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# finalize_research  [terminal]
# ---------------------------------------------------------------------------


class FinalizeResearchInput(BaseModel):
    notes: str = Field(
        default="",
        description="Optional notes about coverage gaps or data limitations to include in the final report.",
    )


class FinalizeResearchTool(AgentTool):
    name: str = "finalize_research"
    description: str = (
        "Mark research as complete and trigger scoring. "
        "Call exactly once when all required pillars have sufficient evidence. "
        "This is a terminal tool — the research run ends after this call."
    )
    args_schema: type[BaseModel] = FinalizeResearchInput
    max_calls_per_run: int = 1

    def _run(self, notes: str = "") -> str:  # type: ignore[override]
        state = self.ctx.state
        ebp = state.get("evidence_by_pillar") or {}
        total = sum(len(v) for v in ebp.values())

        # Signal completion — ResearchAgent.run() reads this flag after the harness exits
        state["finalized"] = True
        state["finalize_notes"] = notes

        logger.info(
            "finalize_research | ticker=%s total_evidence=%d pillars=%d",
            self.ctx.metadata.get("ticker", ""),
            total,
            len(ebp),
        )

        summary_lines = [f"Research finalized. {total} evidence items across {len(ebp)} pillars."]
        for pillar, items in ebp.items():
            summary_lines.append(f"  {pillar}: {len(items)} items")
        if notes:
            summary_lines.append(f"Notes: {notes}")

        return "\n".join(summary_lines)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_research_tools() -> list[AgentTool]:
    return [
        SearchWebTool(),
        StoreEvidenceTool(),
        EvaluateCoverageTool(),
        FinalizeResearchTool(),
    ]
