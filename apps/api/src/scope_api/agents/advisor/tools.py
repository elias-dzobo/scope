"""Advisor Agent tool implementations — fast-only edition.

Design principles (from Anthropic's agentic systems guide):
  - Every tool must complete in under 15 seconds. No synchronous deep research.
  - Tools are thin wrappers around existing infrastructure. No routing logic here.
  - All state lives in AgentContext, never in tool instances.
  - If fresh deep research is needed, queue it and tell the user — don't block.

Tool catalogue:
  get_research_results     DB-only fetch of completed research for a ticker (~20ms)
  search_memory            Semantic search over user's memory graph (~100ms)
  get_user_profile         Fetch full investor profile from DB (~20ms)
  quick_web_search         Direct Exa/Tavily search, no analysis layer (3-8s)
  trigger_background_research  Queue a new deep-research job, return immediately

Why no run_company_research / run_generic_research here:
  Those tools call ResearchController which runs 2-5 minutes of parallel LLM
  calls. The advisor has a 90s timeout. They always timed out. Instead:
  - If research exists in DB → get_research_results (instant)
  - If user needs quick facts → quick_web_search (seconds)
  - If user needs deep analysis → trigger_background_research (returns job ID)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agent_core.tool import AgentTool
from research_core.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_PILLARS = ["Macro & Industry", "Financial Engine", "Valuation"]


# ──────────────────────────────────────────────────────────────────────────────
# get_research_results  (DB-only, ~20ms)
# ──────────────────────────────────────────────────────────────────────────────


class GetResearchResultsInput(BaseModel):
    ticker: str = Field(description="Stock ticker symbol, e.g. AAPL, NVDA, MSFT.")


class GetResearchResultsTool(AgentTool):
    name: str = "get_research_results"
    description: str = (
        "Fetch the most recent completed deep-research report for a stock ticker "
        "from the database. Returns scorecard, investment takeaway, risks, catalysts, "
        "and pillar scores. This is a fast DB read — always call this BEFORE searching "
        "the web or triggering new research."
    )
    args_schema: type[BaseModel] = GetResearchResultsInput
    max_calls_per_run: int = 6

    def _run(self, ticker: str) -> str:  # type: ignore[override]
        from scope_api import db

        ticker = ticker.upper().strip()
        logger.debug("get_research_results | user=%s ticker=%s", self.ctx.user_id, ticker)
        try:
            runs = db.list_runs(
                user_id=self.ctx.user_id,
                ticker=ticker,
                status="completed",
                limit=1,
            )
            if not runs:
                return f"No completed research found for {ticker}."

            run = runs[0]
            run_id = run.get("id", "")
            created = str(run.get("created_at") or "")[:10]

            # Age calculation — agent can reason about staleness
            age_note = ""
            try:
                created_dt = datetime.fromisoformat(
                    str(run.get("created_at") or "")
                ).replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - created_dt).days
                age_note = f" ({age_days} days old)"
            except Exception:
                pass

            # Result is already embedded in the run row
            result = run.get("result") or {}
            if not result:
                # Fetch the full run which includes result_json
                full_run = db.get_run(run_id, user_id=self.ctx.user_id)
                result = (full_run or {}).get("result") or {}

            if not result:
                return f"Research for {ticker} exists (from {created}{age_note}) but results are not yet available."

            synthesis = result.get("final_synthesis") or {}
            scorecard = result.get("scorecard") or {}
            pillars = result.get("pillars") or {}

            lines = [f"RESEARCH: {ticker} — completed {created}{age_note}"]

            score = scorecard.get("overall_score")
            rec = scorecard.get("recommendation")
            if score or rec:
                lines.append(f"Score: {score}  |  Recommendation: {rec}")

            takeaway = str(synthesis.get("investmentTakeaway") or "")[:600]
            if takeaway:
                lines.append(f"Investment takeaway: {takeaway}")

            snapshot = str(synthesis.get("companySnapshot") or "")[:400]
            if snapshot:
                lines.append(f"Company snapshot: {snapshot}")

            risks = list(synthesis.get("mainRisks") or [])[:4]
            if risks:
                lines.append(f"Main risks: {'; '.join(str(r) for r in risks)}")

            catalysts = list(synthesis.get("catalysts") or [])[:3]
            if catalysts:
                lines.append(f"Catalysts: {'; '.join(str(c) for c in catalysts)}")

            if isinstance(pillars, dict) and pillars:
                pillar_lines = [
                    f"  {name}: {str(data.get('score') or data.get('verdict') or '')}"
                    for name, data in list(pillars.items())[:5]
                    if isinstance(data, dict)
                ]
                if pillar_lines:
                    lines.append("Pillar scores:\n" + "\n".join(pillar_lines))

            return "\n".join(lines)

        except Exception as exc:
            logger.error("get_research_results failed | ticker=%s error=%s", ticker, exc)
            return f"Could not retrieve research for {ticker}: {exc}"


# ──────────────────────────────────────────────────────────────────────────────
# search_memory  (~100ms)
# ──────────────────────────────────────────────────────────────────────────────


class SearchMemoryInput(BaseModel):
    query: str = Field(description="Natural language query to search saved research and advisor history.")
    limit: int = Field(default=8, ge=1, le=20, description="Max results to return.")


class SearchMemoryTool(AgentTool):
    name: str = "search_memory"
    description: str = (
        "Search the user's saved research runs, advisor conversation history, and memory graph. "
        "Use when the injected context is not enough and you need broader saved context. "
        "Returns relevant chunks of saved research and prior advisor answers."
    )
    args_schema: type[BaseModel] = SearchMemoryInput
    max_calls_per_run: int = 3

    def _run(self, query: str, limit: int = 8) -> str:  # type: ignore[override]
        from scope_api.memory import retrieve_user_context

        logger.debug("search_memory | user=%s query=%s", self.ctx.user_id, query[:80])
        try:
            result = retrieve_user_context(self.ctx.user_id, query=query, limit=limit)
            pack = result.get("contextPack") or result.get("context", {}).get("contextPack", {})
            chunks = pack.get("chunks") or []
            nodes = pack.get("nodes") or []

            if not chunks and not nodes:
                return "No saved research or conversation history found for this query."

            lines: list[str] = [f"Found {len(chunks)} memory chunks and {len(nodes)} graph nodes.\n"]
            for i, chunk in enumerate(chunks[:limit], 1):
                text = str(chunk.get("text") or chunk.get("content") or "")[:400]
                source = chunk.get("source_type") or chunk.get("sourceType") or "unknown"
                date = str(chunk.get("created_at") or chunk.get("createdAt") or "")[:10]
                lines.append(f"[{i}] ({source}, {date}): {text}")

            for node in nodes[:5]:
                title = node.get("title", "")
                summary = str(node.get("summary") or "")[:200]
                node_type = node.get("node_type") or node.get("nodeType") or ""
                lines.append(f"[graph:{node_type}] {title}: {summary}")

            return "\n".join(lines)

        except Exception as exc:
            logger.error("search_memory failed | error=%s", exc)
            return f"Memory search failed: {exc}"


# ──────────────────────────────────────────────────────────────────────────────
# get_user_profile  (DB-only, ~20ms)
# ──────────────────────────────────────────────────────────────────────────────


class GetUserProfileInput(BaseModel):
    pass  # always fetches current user's profile


class GetUserProfileTool(AgentTool):
    name: str = "get_user_profile"
    description: str = (
        "Fetch the user's full investor profile: risk tolerance, financial situation, "
        "investment goals, time horizon, and preferences. "
        "The injected context already has a one-line hint. Call this only when the "
        "user's personal situation is directly relevant to your answer."
    )
    args_schema: type[BaseModel] = GetUserProfileInput
    max_calls_per_run: int = 2

    def _run(self) -> str:  # type: ignore[override]
        from scope_api import db
        import json

        logger.debug("get_user_profile | user=%s", self.ctx.user_id)
        try:
            profile = db.get_onboarding_profile(self.ctx.user_id)
            if not profile:
                return "No investor profile found. The user has not completed onboarding."

            snapshot = db.build_profile_snapshot(self.ctx.user_id)
            if not snapshot:
                return "Profile exists but could not be parsed."

            financial = snapshot.get("financialProfile") or {}
            risk = snapshot.get("riskProfile") or {}
            investor = snapshot.get("investorContext") or {}
            narrative = snapshot.get("profileNarrative") or {}

            lines = ["USER INVESTOR PROFILE:"]
            if narrative.get("headline"):
                lines.append(f"Summary: {narrative['headline']}")
            if financial:
                lines.append(f"Financial: {json.dumps(financial)}")
            if risk:
                lines.append(f"Risk: {json.dumps(risk)}")
            if investor:
                lines.append(f"Context: {json.dumps(investor)}")
            return "\n".join(lines)

        except Exception as exc:
            logger.error("get_user_profile failed | error=%s", exc)
            return f"Could not retrieve profile: {exc}"


# ──────────────────────────────────────────────────────────────────────────────
# quick_web_search  (Exa/Tavily direct, 3-8 seconds)
# ──────────────────────────────────────────────────────────────────────────────


class QuickWebSearchInput(BaseModel):
    query: str = Field(description="The specific question or topic to search the web for.")


class QuickWebSearchTool(AgentTool):
    name: str = "quick_web_search"
    description: str = (
        "Run a fast web search (3-8 seconds) via Exa or Tavily for current information. "
        "Use this for: recent news, current prices, recent earnings, current events. "
        "Do NOT use this to replace deep research — it gives surface-level facts only. "
        "Only call when the saved research is stale or the user needs a current fact."
    )
    args_schema: type[BaseModel] = QuickWebSearchInput
    max_calls_per_run: int = 3

    def _run(self, query: str) -> str:  # type: ignore[override]
        from provider_integrations.tools.main import search_tool

        logger.debug("quick_web_search | query=%s", query[:80])
        try:
            results = search_tool({"query": query})
            if not results:
                return f"No web results found for: {query}"

            lines = [f"WEB SEARCH RESULTS for: {query}\n"]
            for i, result in enumerate(results[:6], 1):
                title = str(result.get("title") or "")[:100]
                snippet = str(result.get("snippet") or result.get("content") or "")[:300]
                url = str(result.get("link") or result.get("url") or "")
                date = str(result.get("date") or result.get("published_date") or "")[:10]
                date_str = f" ({date})" if date else ""
                lines.append(f"[{i}] {title}{date_str}\n    {snippet}\n    {url}")

            return "\n".join(lines)

        except Exception as exc:
            logger.error("quick_web_search failed | error=%s", exc)
            return f"Web search failed: {exc}"


# ──────────────────────────────────────────────────────────────────────────────
# trigger_background_research  (returns immediately, ~50ms)
# ──────────────────────────────────────────────────────────────────────────────


class TriggerBackgroundResearchInput(BaseModel):
    ticker: str = Field(description="Stock ticker to research, e.g. AAPL.")
    company_name: str = Field(
        default="",
        description="Optional company name. Inferred from ticker if blank.",
    )


class TriggerBackgroundResearchTool(AgentTool):
    name: str = "trigger_background_research"
    description: str = (
        "Queue a deep research job for a ticker and return immediately. "
        "The research runs in the background (2-5 minutes) — you will NOT have the results "
        "in this conversation turn. Tell the user research has been queued and they can "
        "ask you about it once it completes. "
        "Only call this when: (1) get_research_results returned nothing AND "
        "(2) the user explicitly asked for a deep analysis or research report."
    )
    args_schema: type[BaseModel] = TriggerBackgroundResearchInput
    max_calls_per_run: int = 1

    def _run(self, ticker: str, company_name: str = "") -> str:  # type: ignore[override]
        from scope_api import db

        ticker = ticker.upper().strip()
        if not company_name:
            company_name = _company_name_for_ticker(ticker)

        logger.info(
            "trigger_background_research | user=%s ticker=%s",
            self.ctx.user_id,
            ticker,
        )

        # Guard: don't queue if a recent completed run already exists
        try:
            existing = db.list_runs(
                user_id=self.ctx.user_id,
                ticker=ticker,
                status="completed",
                limit=1,
            )
            if existing:
                created = str(existing[0].get("created_at") or "")
                try:
                    created_dt = datetime.fromisoformat(created).replace(tzinfo=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - created_dt).days
                    if age_days < 7:
                        return (
                            f"Recent research for {ticker} already exists from {age_days} days ago. "
                            "Use get_research_results to fetch it — no need to re-run."
                        )
                except Exception:
                    pass
        except Exception:
            pass

        # Guard: don't queue if already queued/running
        try:
            in_flight = db.list_runs(
                user_id=self.ctx.user_id,
                ticker=ticker,
                limit=1,
            )
            if in_flight:
                run_status = in_flight[0].get("status", "")
                if run_status in {"queued", "running"}:
                    run_id = in_flight[0].get("id", "")
                    return (
                        f"Research for {ticker} is already {run_status} (run_id={run_id}). "
                        "The user can check back in a few minutes."
                    )
        except Exception:
            pass

        try:
            run_id = uuid.uuid4().hex
            db.create_run(
                run_id=run_id,
                company_name=company_name,
                ticker=ticker,
                selected_pillars=_DEFAULT_PILLARS,
                user_id=self.ctx.user_id,
            )
            logger.info(
                "Background research queued | run_id=%s ticker=%s user=%s",
                run_id,
                ticker,
                self.ctx.user_id,
            )
            return (
                f"Research for {ticker} ({company_name}) has been queued (run_id={run_id}). "
                "It will take 2-5 minutes to complete. "
                "Once done, the user can ask you about it and you can use get_research_results to fetch the results."
            )
        except Exception as exc:
            logger.error(
                "trigger_background_research failed | ticker=%s error=%s", ticker, exc
            )
            return f"Could not queue research for {ticker}: {exc}"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _company_name_for_ticker(ticker: str) -> str:
    """Best-effort company name — falls back to the ticker itself."""
    try:
        from scope_api.memory import KNOWN_COMPANY_TICKERS

        reverse = {v.upper(): k.title() for k, v in KNOWN_COMPANY_TICKERS.items()}
        return reverse.get(ticker.upper(), ticker)
    except Exception:
        return ticker


def build_advisor_tools() -> list[AgentTool]:
    """Return all advisor tools in declaration order.

    Order matters: the system prompt tells the agent to try them in this sequence.
    Fast, cheap tools first. Background tools last.
    """
    return [
        GetResearchResultsTool(),
        SearchMemoryTool(),
        GetUserProfileTool(),
        QuickWebSearchTool(),
        TriggerBackgroundResearchTool(),
    ]
