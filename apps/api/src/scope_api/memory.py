"""User memory graph indexing and retrieval helpers.

This module is the first GraphRAG foundation slice. It turns completed research
runs into typed graph records and retrievable text chunks. The initial retriever
is deliberately simple: lexical chunk search plus graph metadata. A vector
index can later replace the search implementation behind the same API shape.
"""

from __future__ import annotations

import json
import os
import re
import hashlib
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from scope_api import db
from scope_api.auth.dependencies import require_current_user
from scope_api.auth.models import AuthUser


class ContextRetrievalPlan(BaseModel):
    """Structured plan for retrieving user memory before synthesis or research."""

    query: str
    intent: str
    entities: list[str] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    needed_context: list[str] = Field(default_factory=list, alias="neededContext")
    search_queries: list[str] = Field(default_factory=list, alias="searchQueries")
    freshness_requirement: str = Field(default="standard", alias="freshnessRequirement")
    reasoning: str = ""
    planner_source: str = Field(default="deterministic_fallback", alias="plannerSource")


class ContextCoverageReport(BaseModel):
    """Assessment of whether retrieved memory is enough for the next step."""

    status: str
    has_user_profile: bool = Field(alias="hasUserProfile")
    has_relevant_memory: bool = Field(alias="hasRelevantMemory")
    matched_entities: list[str] = Field(default_factory=list, alias="matchedEntities")
    missing_context: list[str] = Field(default_factory=list, alias="missingContext")
    stale_context: list[str] = Field(default_factory=list, alias="staleContext")
    should_run_deep_research: bool = Field(alias="shouldRunDeepResearch")
    targeted_research_requests: list[dict[str, Any]] = Field(default_factory=list, alias="targetedResearchRequests")
    confidence: str


class PlannedContextResult(BaseModel):
    """Combined retrieval plan, retrieved context, and coverage decision."""

    plan: ContextRetrievalPlan
    context: dict[str, Any]
    coverage: ContextCoverageReport


class ContextPack(BaseModel):
    """Structured GraphRAG context bundle for advisor and research agents."""

    profile_context: dict[str, Any] | None = Field(default=None, alias="profileContext")
    research_context: list[dict[str, Any]] = Field(default_factory=list, alias="researchContext")
    source_context: list[dict[str, Any]] = Field(default_factory=list, alias="sourceContext")
    graph_neighborhood: list[dict[str, Any]] = Field(default_factory=list, alias="graphNeighborhood")
    freshness: dict[str, Any] = Field(default_factory=dict)
    missing_context: list[str] = Field(default_factory=list, alias="missingContext")
    retrieval_policy: dict[str, Any] = Field(default_factory=dict, alias="retrievalPolicy")


MEMORY_NODE_TYPES = {
    "AdvisorRun",
    "Artifact",
    "Company",
    "FinancialProfileSnapshot",
    "GenericResearchResult",
    "Industry",
    "InvestorPreference",
    "PillarAssessment",
    "Recommendation",
    "ResearchRun",
    "RiskProfileSnapshot",
    "Theme",
    "User",
}

MEMORY_EDGE_TYPES = {
    "ANALYZED",
    "HAS_ARTIFACT",
    "HAS_PILLAR_ASSESSMENT",
    "MENTIONS",
    "PRODUCED",
    "RAN_RESEARCH",
    "RELATED_TO",
    "USED_FINANCIAL_PROFILE",
    "USED_RISK_PROFILE",
}


KNOWN_COMPANY_TICKERS = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "tsmc": "TSM",
    "taiwan semiconductor": "TSM",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "netflix": "NFLX",
    "eli lilly": "LLY",
    "lilly": "LLY",
}

THEME_KEYWORDS = {
    "ai infrastructure": ["ai infrastructure", "gpu", "data center", "accelerator", "capex"],
    "semiconductors": ["semiconductor", "chip", "foundry", "wafer", "fab"],
    "interest rates": ["interest rate", "rates", "fed", "inflation", "yield"],
    "valuation": ["valuation", "multiple", "pe ratio", "expensive", "cheap"],
    "cash flow": ["cash flow", "free cash flow", "fcf", "profit", "margin"],
    "debt": ["debt", "leverage", "balance sheet"],
    "dividends and buybacks": ["dividend", "buyback", "capital return"],
    "industry outlook": ["industry", "sector", "market size", "demand"],
}

CONTEXT_BY_INTENT = {
    "company_research": ["user_profile", "prior_company_research", "pillar_assessments", "recommendations", "recent_sources"],
    "portfolio_fit": ["user_profile", "risk_profile", "financial_profile", "prior_company_research", "recommendations"],
    "industry_research": ["user_profile", "industry_context", "company_exposure", "prior_company_research", "recent_sources"],
    "comparison": ["user_profile", "prior_company_research", "pillar_assessments", "relative_valuation", "risks"],
    "clarification": ["user_profile", "prior_research_context", "source_evidence"],
}


def _text(value: Any) -> str:
    """Coerce a result field into compact text suitable for memory chunks."""
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        return " ".join(_text(item) for item in value if _text(item))
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values() if _text(item))
    return str(value)


def _short(value: str, limit: int = 900) -> str:
    """Trim long summaries before storing them on graph nodes."""
    compact = " ".join(value.split())
    return compact[:limit].rstrip()


def _dedupe(values: list[str]) -> list[str]:
    """Return values in first-seen order with empty strings removed."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = value.strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _dedupe_memory_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return memory chunks in first-seen order without duplicate chunk ids."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk_id = str(chunk.get("id") or "")
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        out.append(chunk)
    return out


def _query_terms(query: str) -> list[str]:
    """Extract searchable words from a natural-language query."""
    stop_words = {
        "about",
        "affect",
        "after",
        "against",
        "also",
        "and",
        "are",
        "can",
        "does",
        "for",
        "from",
        "have",
        "how",
        "into",
        "is",
        "it",
        "make",
        "my",
        "of",
        "on",
        "or",
        "should",
        "stock",
        "stocks",
        "that",
        "the",
        "their",
        "this",
        "to",
        "what",
        "when",
        "with",
    }
    terms = re.findall(r"[A-Za-z][A-Za-z0-9&.-]{2,}", query.lower())
    return [term for term in terms if term not in stop_words]


def _infer_intent(query: str) -> str:
    """Infer the retrieval intent from plain language."""
    q = query.lower()
    if any(word in q for word in ["fit me", "for me", "my profile", "my risk", "should i buy"]):
        return "portfolio_fit"
    if any(word in q for word in ["compare", "versus", " vs ", "better than"]):
        return "comparison"
    if any(word in q for word in ["industry", "sector", "market", "affect", "impact", "trend"]):
        return "industry_research"
    if any(word in q for word in ["explain", "why", "what does", "clarify"]):
        return "clarification"
    return "company_research"


def _sort_tickers_by_query_order(tickers: list[str], query: str) -> list[str]:
    """Keep ticker targets in the order the user mentioned them."""
    lower = query.lower()
    positions: dict[str, int] = {}
    for ticker in tickers:
        normalized = ticker.upper()
        ticker_pos = lower.find(normalized.lower())
        company_positions = [
            lower.find(company)
            for company, mapped_ticker in KNOWN_COMPANY_TICKERS.items()
            if mapped_ticker == normalized and lower.find(company) >= 0
        ]
        candidates = [pos for pos in [ticker_pos, *company_positions] if pos >= 0]
        positions[normalized] = min(candidates) if candidates else len(lower) + len(positions)
    return sorted(tickers, key=lambda item: positions.get(item.upper(), len(lower)))


def _parse_datetime(value: Any) -> datetime | None:
    """Parse the timestamp shapes stored by research, sources, and DB rows."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _env_int(name: str, default: int) -> int:
    """Read a positive integer env var without making config import cycles."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _freshness_threshold_days(plan: ContextRetrievalPlan) -> int:
    """Choose how quickly retrieved context should be considered stale."""
    if plan.intent in {"industry_research", "comparison"} or plan.themes:
        return _env_int("SCOPE_THEMATIC_MEMORY_STALE_DAYS", 90)
    if plan.intent == "clarification":
        return _env_int("SCOPE_CLARIFICATION_MEMORY_STALE_DAYS", 365)
    return _env_int("SCOPE_MEMORY_STALE_DAYS", 180)


def _first_timestamp(*values: Any) -> datetime | None:
    """Return the first parseable timestamp from ordered candidates."""
    for value in values:
        parsed = _parse_datetime(value)
        if parsed:
            return parsed
    return None


def _memory_node_timestamp(node: dict[str, Any]) -> datetime | None:
    """Find the best as-of date for a memory node."""
    properties = dict(node.get("properties") or {})
    source_ref = dict(node.get("source_ref") or {})
    return _first_timestamp(
        properties.get("asOfDate"),
        properties.get("as_of_date"),
        properties.get("sourceDate"),
        properties.get("source_date"),
        properties.get("publishedAt"),
        properties.get("published_at"),
        properties.get("completedAt"),
        properties.get("generatedAt"),
        properties.get("profileUpdatedAt"),
        source_ref.get("retrievedAt"),
        source_ref.get("generatedAt"),
        source_ref.get("completedAt"),
        node.get("updated_at"),
        node.get("created_at"),
    )


def _memory_chunk_timestamp(chunk: dict[str, Any]) -> datetime | None:
    """Find the best as-of date for a memory chunk."""
    metadata = dict(chunk.get("metadata") or {})
    return _first_timestamp(
        metadata.get("asOfDate"),
        metadata.get("as_of_date"),
        metadata.get("sourceDate"),
        metadata.get("source_date"),
        metadata.get("publishedAt"),
        metadata.get("published_at"),
        metadata.get("retrievedAt"),
        metadata.get("retrieved_at"),
        metadata.get("generatedAt"),
        metadata.get("completedAt"),
        metadata.get("createdAt"),
        chunk.get("created_at"),
    )


def _freshness_label(item: dict[str, Any], fallback: str) -> str:
    """Produce a compact label for freshness reports and targeted retries."""
    return str(
        item.get("title")
        or item.get("source_id")
        or item.get("external_id")
        or item.get("node_id")
        or item.get("id")
        or fallback
    )


def assess_context_freshness(plan: ContextRetrievalPlan, context: dict[str, Any]) -> dict[str, Any]:
    """Assess whether retrieved memory is recent enough for the current query.

    The retriever can still use old context as background, but stale context
    should trigger a targeted refresh before making investment-sensitive claims.
    """
    threshold_days = _freshness_threshold_days(plan)
    now = datetime.now(timezone.utc)
    stale_items: list[dict[str, Any]] = []
    timestamps: list[datetime] = []

    for node in context.get("matchedNodes") or []:
        timestamp = _memory_node_timestamp(node)
        if not timestamp:
            continue
        timestamps.append(timestamp)
        age_days = max((now - timestamp).days, 0)
        if age_days > threshold_days:
            label = _freshness_label(node, "memory node")
            stale_items.append(
                {
                    "kind": "node",
                    "label": label,
                    "asOf": timestamp.isoformat(),
                    "ageDays": age_days,
                    "thresholdDays": threshold_days,
                    "summary": f"{label} is {age_days} days old",
                }
            )

    for chunk in context.get("chunks") or []:
        timestamp = _memory_chunk_timestamp(chunk)
        if not timestamp:
            continue
        timestamps.append(timestamp)
        age_days = max((now - timestamp).days, 0)
        if age_days > threshold_days:
            label = _freshness_label(chunk, "memory chunk")
            stale_items.append(
                {
                    "kind": "chunk",
                    "label": label,
                    "asOf": timestamp.isoformat(),
                    "ageDays": age_days,
                    "thresholdDays": threshold_days,
                    "summary": f"{label} is {age_days} days old",
                }
            )

    freshest = max(timestamps).isoformat() if timestamps else None
    oldest = min(timestamps).isoformat() if timestamps else None
    return {
        "thresholdDays": threshold_days,
        "freshestAt": freshest,
        "oldestAt": oldest,
        "staleItems": stale_items,
        "hasKnownDates": bool(timestamps),
    }


CONTEXT_PLANNER_MODEL = os.getenv("CONTEXT_PLANNER_MODEL", "gpt-4o-mini")

CONTEXT_PLANNER_SYSTEM = """You plan GraphRAG memory retrieval for an investment research app.

Return a compact retrieval plan. Identify tickers only when explicit or strongly implied.
For broad industry, market, sector, or macro questions, prefer industry_research or comparison.
For user suitability questions, include user_profile, risk_profile, and financial_profile.
Do not include raw user financial details. Keep searchQueries short and useful."""


def build_context_retrieval_plan(user_id: str, query: str) -> ContextRetrievalPlan:
    """Plan what memory context should be fetched for a user query."""
    fallback = _build_context_retrieval_plan_deterministic(user_id, query)
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return fallback

    try:
        known_companies = [
            {
                "title": node.get("title", ""),
                "ticker": node.get("properties", {}).get("ticker") or node.get("external_id", ""),
            }
            for node in db.list_memory_nodes(user_id, node_type="Company", limit=100)
        ]
        model = ChatOpenAI(model=CONTEXT_PLANNER_MODEL, temperature=0)
        structured = model.with_structured_output(ContextRetrievalPlan, method="function_calling")
        parsed = structured.invoke(
            [
                ("system", CONTEXT_PLANNER_SYSTEM),
                (
                    "human",
                    json.dumps(
                        {
                            "query": query,
                            "knownCompanyMemory": known_companies[:40],
                            "allowedIntents": list(CONTEXT_BY_INTENT),
                            "deterministicBaseline": fallback.model_dump(mode="json", by_alias=True),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        plan = parsed if isinstance(parsed, ContextRetrievalPlan) else ContextRetrievalPlan.model_validate(parsed)
        return _validate_context_plan(plan, fallback)
    except Exception:
        return fallback


def _build_context_retrieval_plan_deterministic(user_id: str, query: str) -> ContextRetrievalPlan:
    """Deterministic fallback planner for local/dev operation and LLM failures."""
    q = query.strip()
    lower = q.lower()
    ticker_stop_words = {"AI", "CEO", "CFO", "EPS", "ETF", "FCF", "GDP", "IPO", "PE"}
    tickers = [ticker for ticker in re.findall(r"\b[A-Z]{2,5}\b", q) if ticker not in ticker_stop_words]
    entities: list[str] = []
    for company, ticker in KNOWN_COMPANY_TICKERS.items():
        if company in lower:
            entities.append(company.title())
            tickers.append(ticker)
    for node in db.list_memory_nodes(user_id, node_type="Company", limit=100):
        title = str(node.get("title", ""))
        ticker = str(node.get("properties", {}).get("ticker") or node.get("external_id") or "")
        if title and title.lower().split(" (")[0] in lower:
            entities.append(title)
            tickers.append(ticker)
        elif ticker and ticker.upper() in tickers:
            entities.append(title or ticker)

    themes = []
    for theme, keywords in THEME_KEYWORDS.items():
        if any(keyword in lower for keyword in keywords):
            themes.append(theme)

    intent = _infer_intent(q)
    latest_generic = _latest_generic_research_node(user_id) if _is_follow_up_research_query(lower) else None
    if latest_generic:
        intent = "industry_research"
        properties = latest_generic.get("properties") or {}
        themes.extend(str(theme) for theme in properties.get("themes", []) if str(theme).strip())
    needed_context = list(CONTEXT_BY_INTENT[intent])
    if themes and "theme_context" not in needed_context:
        needed_context.append("theme_context")
    if not tickers and intent in {"industry_research", "comparison"}:
        needed_context.append("entity_discovery")
    if latest_generic:
        needed_context = [
            item
            for item in needed_context
            if item not in {"user_profile", "entity_discovery", "prior_company_research"}
        ]

    search_queries = [q]
    if latest_generic:
        latest_props = latest_generic.get("properties") or {}
        search_queries.append(str(latest_props.get("query") or latest_generic.get("title") or ""))
        search_queries.extend(str(theme) for theme in latest_props.get("themes", []) if str(theme).strip())
    search_queries.extend(tickers)
    search_queries.extend(themes)
    search_queries.extend(_query_terms(q)[:6])

    return ContextRetrievalPlan(
        query=q,
        intent=intent,
        entities=_dedupe(entities),
        tickers=_sort_tickers_by_query_order(_dedupe([ticker.upper() for ticker in tickers]), q),
        themes=_dedupe(themes),
        neededContext=_dedupe(needed_context),
        searchQueries=_dedupe(search_queries),
        freshnessRequirement="high" if intent in {"industry_research", "comparison"} else "standard",
        reasoning="Deterministic fallback plan based on query terms, known company memory, and intent keywords.",
        plannerSource="deterministic_fallback",
    )


def _is_follow_up_research_query(lower_query: str) -> bool:
    """Detect short advisor follow-ups that refer to the previous research result."""
    follow_up_markers = [
        "this result",
        "the result",
        "that result",
        "break this",
        "break it down",
        "companies should i",
        "companies to watch",
        "lookout for",
        "be on the lookout",
    ]
    return any(marker in lower_query for marker in follow_up_markers)


def _latest_generic_research_node(user_id: str) -> dict[str, Any] | None:
    """Return the latest saved generic research node for follow-up questions."""
    nodes = db.list_memory_nodes(user_id, node_type="GenericResearchResult", limit=1)
    return nodes[0] if nodes else None


def _active_research_run_ids(query: str) -> list[str]:
    """Extract explicit active research run ids from thread-augmented queries."""
    ids = re.findall(r"Active research run id:\s*([A-Za-z0-9\-]+)", query)
    return _dedupe(ids)


def _validate_context_plan(plan: ContextRetrievalPlan, fallback: ContextRetrievalPlan) -> ContextRetrievalPlan:
    """Normalize and constrain an LLM retrieval plan before execution."""
    intent = plan.intent if plan.intent in CONTEXT_BY_INTENT else fallback.intent
    needed = _dedupe([item for item in plan.needed_context if item])[:8] or fallback.needed_context
    ticker_stop_words = {"AI", "CEO", "CFO", "EPS", "ETF", "FCF", "GDP", "IPO", "PE"}
    tickers = _sort_tickers_by_query_order(
        _dedupe(
            [
                re.sub(r"[^A-Za-z0-9.]", "", ticker).upper()
                for ticker in plan.tickers
                if 1 < len(re.sub(r"[^A-Za-z0-9.]", "", ticker)) <= 6
                and re.sub(r"[^A-Za-z0-9.]", "", ticker).upper() not in ticker_stop_words
            ]
        ),
        plan.query or fallback.query,
    )[:8]
    query = (plan.query or fallback.query).strip()
    for run_id in _active_research_run_ids(fallback.query):
        if run_id not in query:
            query = f"{query}\n\nActive research run id: {run_id}."
    search_queries = _dedupe([query, *plan.search_queries, *tickers, *plan.themes])[:12]
    return ContextRetrievalPlan(
        query=query,
        intent=intent,
        entities=_dedupe(plan.entities)[:8],
        tickers=tickers,
        themes=_dedupe(plan.themes)[:8],
        neededContext=needed,
        searchQueries=search_queries,
        freshnessRequirement=plan.freshness_requirement or fallback.freshness_requirement,
        reasoning=plan.reasoning or "LLM context planner selected memory targets.",
        plannerSource="llm",
    )


def _chunk_text(node: dict[str, Any], *, source_type: str, source_id: str, text: str, metadata: dict[str, Any]) -> None:
    """Create a memory chunk when meaningful text exists."""
    body = _short(text, 2200)
    if not body:
        return
    metadata = dict(metadata)
    embedding_provider = os.getenv("SCOPE_MEMORY_EMBEDDING_PROVIDER", "").strip()
    if embedding_provider:
        metadata["embedding"] = {
            "provider": embedding_provider,
            "status": "pending",
            "textHash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }
    db.create_memory_chunk(
        user_id=node["user_id"],
        node_id=node["id"],
        source_type=source_type,
        source_id=source_id,
        text=body,
        metadata=metadata,
    )


def index_completed_research_run(
    *,
    run: dict[str, Any],
    result_payload: dict[str, Any],
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Index a completed research run into the user's memory graph.

    Anonymous runs are intentionally skipped because memory is a user-owned
    personalization surface. This keeps future advisor retrieval scoped to a
    signed-in user's profile and research history.
    """
    user_id = str(run.get("user_id") or "")
    if not user_id:
        return {"nodes": 0, "edges": 0, "chunks": 0, "skipped": 1}

    run_id = str(run["id"])
    ticker = str(run.get("ticker") or result_payload.get("summary", {}).get("ticker") or "").upper()
    company_name = str(run.get("company_name") or result_payload.get("summary", {}).get("stock_name") or ticker)
    selected_pillars = list(run.get("selected_pillars") or [])
    profile_snapshot = dict(run.get("profile_snapshot") or result_payload.get("profileSnapshot") or {})
    final_synthesis = dict(result_payload.get("finalSynthesis") or {})
    scorecard = dict(result_payload.get("scorecard") or {})
    pillar_assessments = dict(result_payload.get("pillarAssessments") or {})
    artifacts = artifacts or []

    source_ref = {"sourceType": "research_run", "runId": run_id, "ticker": ticker}
    counters = {"nodes": 0, "edges": 0, "chunks": 0, "skipped": 0}

    user_node = db.upsert_memory_node(
        user_id=user_id,
        node_type="User",
        external_id=user_id,
        title="User profile",
        summary=_short(_text(profile_snapshot.get("profileNarrative") or profile_snapshot.get("investorContext"))),
        properties={"hasProfileSnapshot": bool(profile_snapshot)},
        source_ref={"sourceType": "profile_snapshot", "runId": run_id},
    )
    company_node = db.upsert_memory_node(
        user_id=user_id,
        node_type="Company",
        external_id=ticker,
        title=f"{company_name} ({ticker})" if ticker else company_name,
        summary=_short(final_synthesis.get("companySnapshot") or result_payload.get("summary", {}).get("stock_name", "")),
        properties={"ticker": ticker, "companyName": company_name},
        source_ref=source_ref,
    )
    run_node = db.upsert_memory_node(
        user_id=user_id,
        node_type="ResearchRun",
        external_id=run_id,
        title=f"{ticker} research run" if ticker else "Research run",
        summary=_short(final_synthesis.get("investmentTakeaway") or _text(result_payload.get("summary"))),
        properties={
            "ticker": ticker,
            "companyName": company_name,
            "selectedPillars": selected_pillars,
            "overallScore": result_payload.get("summary", {}).get("overall_score"),
            "recommendation": result_payload.get("summary", {}).get("recommendation"),
            "completedAt": run.get("completed_at", ""),
        },
        source_ref=source_ref,
    )
    counters["nodes"] += 3

    for edge in [
        db.upsert_memory_edge(
            user_id=user_id,
            source_node_id=user_node["id"],
            target_node_id=run_node["id"],
            edge_type="RAN_RESEARCH",
            source_ref=source_ref,
        ),
        db.upsert_memory_edge(
            user_id=user_id,
            source_node_id=run_node["id"],
            target_node_id=company_node["id"],
            edge_type="ANALYZED",
            source_ref=source_ref,
        ),
    ]:
        counters["edges"] += 1 if edge else 0

    if profile_snapshot:
        investor_context = dict(profile_snapshot.get("investorContext") or {})
        risk_node = db.upsert_memory_node(
            user_id=user_id,
            node_type="RiskProfileSnapshot",
            external_id=f"{run_id}:risk_profile",
            title="Risk profile used for research",
            summary=_short(_text(profile_snapshot.get("riskProfile"))),
            properties=profile_snapshot.get("riskProfile") or {},
            source_ref=source_ref,
        )
        financial_node = db.upsert_memory_node(
            user_id=user_id,
            node_type="FinancialProfileSnapshot",
            external_id=f"{run_id}:financial_profile",
            title="Financial profile used for research",
            summary=_short(_text(profile_snapshot.get("financialProfile"))),
            properties=profile_snapshot.get("financialProfile") or {},
            source_ref=source_ref,
        )
        counters["nodes"] += 2
        for node, edge_type in [(risk_node, "USED_RISK_PROFILE"), (financial_node, "USED_FINANCIAL_PROFILE")]:
            db.upsert_memory_edge(
                user_id=user_id,
                source_node_id=run_node["id"],
                target_node_id=node["id"],
                edge_type=edge_type,
                source_ref=source_ref,
            )
            counters["edges"] += 1
        preference_values = [
            ("primaryGoal", investor_context.get("primaryGoal")),
            ("preferredStyle", investor_context.get("preferredStyle")),
            *[("preferredMarket", item) for item in investor_context.get("preferredMarkets", [])],
            *[("preferredCompany", item) for item in investor_context.get("preferredCompanies", [])],
            *[("restrictedSector", item) for item in investor_context.get("restrictedSectors", [])],
        ]
        for pref_type, value in preference_values:
            normalized = str(value or "").strip()
            if not normalized:
                continue
            pref_node = db.upsert_memory_node(
                user_id=user_id,
                node_type="InvestorPreference",
                external_id=f"{pref_type}:{normalized.lower()}",
                title=normalized.replace("_", " ").title(),
                summary=f"Investor preference from profile snapshot: {normalized}",
                properties={"preferenceType": pref_type, "value": normalized},
                source_ref=source_ref,
            )
            db.upsert_memory_edge(
                user_id=user_id,
                source_node_id=user_node["id"],
                target_node_id=pref_node["id"],
                edge_type="RELATED_TO",
                source_ref=source_ref,
            )
            counters["nodes"] += 1
            counters["edges"] += 1

    recommendation = final_synthesis.get("personalizedRecommendation") or {}
    if recommendation:
        final_action = recommendation.get("finalRecommendation") or {}
        rec_node = db.upsert_memory_node(
            user_id=user_id,
            node_type="Recommendation",
            external_id=f"{run_id}:recommendation",
            title=str(final_action.get("action") or result_payload.get("summary", {}).get("recommendation") or "Recommendation"),
            summary=_short(final_action.get("explanation") or final_synthesis.get("investmentTakeaway") or ""),
            properties=recommendation,
            source_ref=source_ref,
        )
        db.upsert_memory_edge(
            user_id=user_id,
            source_node_id=run_node["id"],
            target_node_id=rec_node["id"],
            edge_type="PRODUCED",
            source_ref=source_ref,
        )
        counters["nodes"] += 1
        counters["edges"] += 1

    _chunk_text(
        run_node,
        source_type="final_synthesis",
        source_id=run_id,
        text=" ".join(
            [
                final_synthesis.get("companySnapshot", ""),
                final_synthesis.get("investmentTakeaway", ""),
                _text(final_synthesis.get("recommendationRationale")),
                _text(final_synthesis.get("mainRisks")),
                final_synthesis.get("bottomLine", ""),
            ]
        ),
        metadata={"ticker": ticker, "companyName": company_name, "section": "final_synthesis"},
    )
    counters["chunks"] += 1

    pillar_takeaways = final_synthesis.get("pillarTakeaways") or []
    for pillar in pillar_takeaways:
        pillar_name = str(pillar.get("pillarName") or pillar.get("pillar_name") or "")
        if not pillar_name:
            continue
        pillar_node = db.upsert_memory_node(
            user_id=user_id,
            node_type="PillarAssessment",
            external_id=f"{run_id}:pillar:{pillar_name}",
            title=f"{ticker} {pillar_name}" if ticker else pillar_name,
            summary=_short(pillar.get("plainEnglishSummary") or pillar.get("position") or ""),
            properties={
                "pillar": pillar_name,
                "score": pillar.get("score"),
                "position": pillar.get("position"),
                "technicalDetails": pillar.get("technicalDetails") or "",
                "legacyAssessment": pillar_assessments.get(pillar_name, {}),
            },
            source_ref=source_ref,
        )
        db.upsert_memory_edge(
            user_id=user_id,
            source_node_id=run_node["id"],
            target_node_id=pillar_node["id"],
            edge_type="HAS_PILLAR_ASSESSMENT",
            source_ref=source_ref,
        )
        counters["nodes"] += 1
        counters["edges"] += 1
        _chunk_text(
            pillar_node,
            source_type="pillar_takeaway",
            source_id=f"{run_id}:{pillar_name}",
            text=" ".join(
                [
                    pillar_name,
                    pillar.get("plainEnglishSummary", ""),
                    pillar.get("whyItMatters", ""),
                    _text(pillar.get("supportingPoints")),
                    _text(pillar.get("watchItems")),
                    pillar.get("technicalDetails", ""),
                ]
            ),
            metadata={"ticker": ticker, "companyName": company_name, "pillar": pillar_name},
        )
        counters["chunks"] += 1

    if scorecard:
        _chunk_text(
            run_node,
            source_type="scorecard",
            source_id=run_id,
            text=_text(scorecard),
            metadata={"ticker": ticker, "companyName": company_name, "section": "scorecard"},
        )
        counters["chunks"] += 1

    for artifact in artifacts:
        artifact_node = db.upsert_memory_node(
            user_id=user_id,
            node_type="Artifact",
            external_id=str(artifact.get("id") or artifact.get("storage_uri") or ""),
            title=str(artifact.get("artifact_type") or "Research artifact"),
            summary=str(artifact.get("storage_uri") or ""),
            properties=artifact,
            source_ref=source_ref,
        )
        db.upsert_memory_edge(
            user_id=user_id,
            source_node_id=run_node["id"],
            target_node_id=artifact_node["id"],
            edge_type="HAS_ARTIFACT",
            source_ref=source_ref,
        )
        counters["nodes"] += 1
        counters["edges"] += 1

    return counters


def _search_chunks_for_queries(user_id: str, queries: list[str], limit: int) -> list[dict[str, Any]]:
    """Search memory chunks across multiple query strings with dedupe."""
    chunks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        for chunk in db.search_memory_chunks(user_id, query=query, limit=limit):
            if chunk["id"] in seen:
                continue
            seen.add(chunk["id"])
            chunks.append(chunk)
            if len(chunks) >= limit:
                return chunks
    return chunks


def _profile_context(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return safe profile context without raw onboarding answers."""
    if not profile:
        return None
    return {
        "financialProfile": profile["financial_profile"],
        "riskProfile": profile["risk_profile"],
        "investorContext": profile["investor_context"],
        "profileNarrative": profile["profile_narrative"],
        "confidence": profile["confidence"],
    }


def _lexical_score(query_terms: list[str], text: str) -> float:
    """Score how much a chunk/node text overlaps the query terms."""
    if not query_terms:
        return 0.0
    lower = text.lower()
    matches = sum(1 for term in query_terms if term in lower)
    return min(matches / max(len(query_terms), 1), 1.0)


def _entity_score(plan: ContextRetrievalPlan, item: dict[str, Any]) -> float:
    """Reward chunks and nodes that mention planned tickers/entities/themes."""
    blob = _text(item).lower()
    needles = [*plan.tickers, *plan.entities, *plan.themes]
    if not needles:
        return 0.0
    matches = sum(1 for needle in needles if needle and needle.lower() in blob)
    return min(matches / len(needles), 1.0)


def _freshness_score(freshness: dict[str, Any], item: dict[str, Any]) -> float:
    """Reward fresh context and softly penalize stale context."""
    stale_labels = {
        str(stale.get("label", "")).lower()
        for stale in freshness.get("staleItems", [])
    }
    label = _freshness_label(item, "").lower()
    if label and label in stale_labels:
        return 0.0
    if freshness.get("hasKnownDates"):
        return 1.0
    return 0.5


def _profile_relevance_score(profile_context: dict[str, Any] | None, item: dict[str, Any]) -> float:
    """Reward context touching user goals, preferences, and risk language."""
    if not profile_context:
        return 0.0
    investor = profile_context.get("investorContext") or {}
    risk = profile_context.get("riskProfile") or {}
    blob = _text(item).lower()
    needles = [
        investor.get("primaryGoal", ""),
        investor.get("preferredStyle", ""),
        risk.get("riskTolerance", ""),
        risk.get("riskCapacity", ""),
        *_text(investor.get("preferredCompanies", [])).split(),
        *_text(investor.get("restrictedSectors", [])).split(),
    ]
    needles = [needle for needle in needles if needle]
    if not needles:
        return 0.0
    matches = sum(1 for needle in needles if needle.lower() in blob)
    return min(matches / len(needles), 1.0)


def _score_retrieved_context(
    *,
    plan: ContextRetrievalPlan,
    chunks: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    profile_context: dict[str, Any] | None,
    freshness: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach transparent retrieval scores to chunks and matched graph nodes."""
    query_terms = _query_terms(plan.query)

    def score_item(item: dict[str, Any], graph_proximity: float) -> dict[str, Any]:
        lexical = _lexical_score(query_terms, f"{item.get('text', '')} {_text(item.get('metadata'))} {item.get('title', '')} {_text(item.get('properties'))}")
        entity = _entity_score(plan, item)
        fresh = _freshness_score(freshness, item)
        profile = _profile_relevance_score(profile_context, item)
        total = round((lexical * 0.35) + (entity * 0.3) + (fresh * 0.2) + (graph_proximity * 0.1) + (profile * 0.05), 3)
        out = dict(item)
        out["retrievalScore"] = total
        out["retrievalScoreDetail"] = {
            "lexical": round(lexical, 3),
            "entity": round(entity, 3),
            "freshness": round(fresh, 3),
            "graphProximity": round(graph_proximity, 3),
            "profile": round(profile, 3),
        }
        return out

    scored_chunks = [score_item(chunk, 1.0) for chunk in chunks]
    scored_nodes = [score_item(node, 0.75) for node in nodes]
    return (
        sorted(scored_chunks, key=lambda item: item.get("retrievalScore", 0), reverse=True),
        sorted(scored_nodes, key=lambda item: item.get("retrievalScore", 0), reverse=True),
    )


def _source_context(chunks: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Build compact source context from retrieved chunks."""
    sources = []
    for chunk in chunks[:limit]:
        sources.append(
            {
                "sourceType": chunk.get("source_type"),
                "sourceId": chunk.get("source_id"),
                "nodeTitle": chunk.get("node_title", ""),
                "metadata": chunk.get("metadata", {}),
                "excerpt": _short(chunk.get("text", ""), 360),
                "retrievalScore": chunk.get("retrievalScore", 0),
            }
        )
    return sources


def _build_context_pack(
    *,
    profile_context: dict[str, Any] | None,
    chunks: list[dict[str, Any]],
    matched_nodes: list[dict[str, Any]],
    neighbors: list[dict[str, Any]],
    freshness: dict[str, Any],
    missing_context: list[str] | None = None,
) -> dict[str, Any]:
    """Create the canonical GraphRAG context bundle."""
    return ContextPack(
        profileContext=profile_context,
        researchContext=chunks,
        sourceContext=_source_context(chunks),
        graphNeighborhood=[*matched_nodes, *neighbors],
        freshness=freshness,
        missingContext=missing_context or [],
        retrievalPolicy={
            "version": "graphrag_v1_lexical_graph_freshness",
            "embeddingStatus": "metadata_only" if os.getenv("SCOPE_MEMORY_EMBEDDING_PROVIDER") else "disabled",
            "scoreWeights": {"lexical": 0.35, "entity": 0.3, "freshness": 0.2, "graphProximity": 0.1, "profile": 0.05},
        },
    ).model_dump(mode="json", by_alias=True)


def assess_context_coverage(user_id: str, plan: ContextRetrievalPlan, context: dict[str, Any]) -> ContextCoverageReport:
    """Decide whether retrieved memory is enough or deep research is required."""
    chunks = list(context.get("chunks") or [])
    nodes = list(context.get("matchedNodes") or [])
    profile = context.get("userProfile") or {}
    matched_entities: list[str] = []
    node_blob = " ".join([_text(node.get("title")) + " " + _text(node.get("properties")) for node in nodes]).lower()
    chunk_blob = " ".join([_text(chunk.get("text")) + " " + _text(chunk.get("metadata")) for chunk in chunks]).lower()
    combined_blob = f"{node_blob} {chunk_blob}"

    for ticker in plan.tickers:
        if ticker.lower() in combined_blob:
            matched_entities.append(ticker)
    for entity in plan.entities:
        if entity.lower() in combined_blob:
            matched_entities.append(entity)

    missing_context: list[str] = []
    if "user_profile" in plan.needed_context and not profile:
        missing_context.append("user_profile")
    if plan.tickers and not matched_entities:
        missing_context.append("mentioned_entities")
    if "entity_discovery" in plan.needed_context and not plan.tickers and not matched_entities:
        missing_context.append("entity_discovery")
    if "theme_context" in plan.needed_context:
        for theme in plan.themes:
            if theme.lower() not in combined_blob:
                missing_context.append(f"theme_context:{theme}")
    if not chunks:
        missing_context.append("retrievable_evidence")
    has_generic_research = any(chunk.get("source_type") == "generic_research_result" for chunk in chunks)
    if plan.intent in {"industry_research", "comparison"} and len(chunks) < 2 and not has_generic_research:
        missing_context.append("multi_source_context")

    freshness = context.get("freshness") or assess_context_freshness(plan, context)
    stale_context = [
        str(item.get("summary") or item.get("label") or "stale_context")
        for item in freshness.get("staleItems") or []
    ]

    should_research = bool(missing_context or stale_context)
    targeted_requests = []
    if should_research:
        targets = plan.tickers or plan.entities or plan.themes or [_short(plan.query, 80)]
        for missing in _dedupe(missing_context + stale_context):
            targeted_requests.append(
                {
                    "reason": missing,
                    "targets": targets,
                    "query": f"{plan.query} {missing.replace(':', ' ')}".strip(),
                    "preferredSourceTypes": ["primary_documents", "grounded_search", "recent_sources"],
                }
            )

    status = "sufficient"
    confidence = "high"
    if missing_context or stale_context:
        status = "needs_research"
        confidence = "low" if len(missing_context) >= 2 else "medium"
    elif len(chunks) < 2 and plan.intent != "clarification":
        status = "partial"
        confidence = "medium"

    return ContextCoverageReport(
        status=status,
        hasUserProfile=bool(profile),
        hasRelevantMemory=bool(chunks or nodes),
        matchedEntities=_dedupe(matched_entities),
        missingContext=_dedupe(missing_context),
        staleContext=_dedupe(stale_context),
        shouldRunDeepResearch=should_research,
        targetedResearchRequests=targeted_requests,
        confidence=confidence,
    )


def retrieve_user_context(
    user_id: str,
    query: str,
    limit: int = 10,
    plan: ContextRetrievalPlan | None = None,
) -> dict[str, Any]:
    """Retrieve memory context for advisor/research planning use-cases."""
    active_plan = plan or build_context_retrieval_plan(user_id, query)
    chunks = _search_chunks_for_queries(user_id, active_plan.search_queries, limit)
    anchored_nodes: list[dict[str, Any]] = []
    for run_id in _active_research_run_ids(active_plan.query):
        for node in db.list_memory_nodes(user_id, node_type="ResearchRun", limit=100):
            if node.get("external_id") == run_id:
                anchored_nodes.append(node)
                chunks = _dedupe_memory_chunks(
                    [*db.list_memory_chunks_for_node(user_id, node["id"], limit=limit), *chunks]
                )[:limit]
                break
    if _is_follow_up_research_query(active_plan.query.lower()):
        recent_generic_chunks = [
            chunk
            for chunk in db.search_memory_chunks(user_id, "", limit=limit)
            if chunk.get("source_type") == "generic_research_result"
        ]
        chunks = _dedupe_memory_chunks([*recent_generic_chunks, *chunks])[:limit]
    if not chunks and active_plan.query.strip():
        # First pass is an exact lexical phrase lookup. Fall back to individual
        # query terms so natural questions still find relevant saved research.
        chunks = _search_chunks_for_queries(user_id, _query_terms(active_plan.query), limit)
    node_ids = {chunk["node_id"] for chunk in chunks}
    neighbors: list[dict[str, Any]] = []
    for node_id in list(node_ids)[:5]:
        neighbors.extend(db.get_memory_neighbors(user_id, node_id, limit=5))
    matched_nodes: list[dict[str, Any]] = list(anchored_nodes)
    for node_type in [
        "GenericResearchResult",
        "Theme",
        "Industry",
        "Company",
        "ResearchRun",
        "Recommendation",
        "PillarAssessment",
    ]:
        for node in db.list_memory_nodes(user_id, node_type=node_type, limit=100):
            haystack = f"{node.get('title', '')} {node.get('external_id', '')} {_text(node.get('properties'))}".lower()
            needles = [*active_plan.tickers, *active_plan.entities, *active_plan.themes, *_query_terms(active_plan.query)[:4]]
            if any(needle.lower() in haystack for needle in needles):
                matched_nodes.append(node)
                if len(matched_nodes) >= limit:
                    break
        if len(matched_nodes) >= limit:
            break
    user_profile = db.get_onboarding_profile(user_id)
    safe_profile = _profile_context(user_profile)
    base_context = {
        "chunks": chunks,
        "matchedNodes": matched_nodes,
    }
    freshness = assess_context_freshness(active_plan, base_context)
    scored_chunks, scored_nodes = _score_retrieved_context(
        plan=active_plan,
        chunks=chunks,
        nodes=matched_nodes,
        profile_context=safe_profile,
        freshness=freshness,
    )
    context = {
        "query": active_plan.query,
        "plan": active_plan.model_dump(mode="json", by_alias=True),
        "userProfile": safe_profile,
        "chunks": scored_chunks,
        "neighbors": neighbors,
        "matchedNodes": scored_nodes,
        "coverage": {
            "hasMemory": bool(chunks or neighbors),
            "chunkCount": len(chunks),
            "neighborCount": len(neighbors),
            "matchedNodeCount": len(matched_nodes),
        },
    }
    context["freshness"] = freshness
    context["contextPack"] = _build_context_pack(
        profile_context=safe_profile,
        chunks=scored_chunks,
        matched_nodes=scored_nodes,
        neighbors=neighbors,
        freshness=freshness,
    )
    return context


def plan_user_context(user_id: str, query: str, limit: int = 10) -> dict[str, Any]:
    """Plan, retrieve, and assess user context for a future advisor turn."""
    plan = build_context_retrieval_plan(user_id, query)
    context = retrieve_user_context(user_id, query=query, limit=limit, plan=plan)
    coverage = assess_context_coverage(user_id, plan, context)
    context["contextPack"]["missingContext"] = coverage.missing_context
    result = PlannedContextResult(plan=plan, context=context, coverage=coverage)
    return result.model_dump(mode="json", by_alias=True)


def build_memory_router(api_prefix: str) -> APIRouter:
    """Build user-memory endpoints under the versioned API prefix."""
    router = APIRouter(prefix=f"{api_prefix}/memory", tags=["memory"])

    @router.get("/search")
    async def search_memory(
        q: str = Query(default="", min_length=0),
        limit: int = Query(default=10, ge=1, le=50),
        current_user: AuthUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        """Search the signed-in user's saved research memory."""
        return retrieve_user_context(current_user.id, query=q, limit=limit)

    @router.get("/context-plan")
    async def context_plan(
        q: str = Query(default="", min_length=1),
        limit: int = Query(default=10, ge=1, le=50),
        current_user: AuthUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        """Plan retrieval and report whether memory is enough to answer."""
        return plan_user_context(current_user.id, query=q, limit=limit)

    @router.get("/nodes")
    async def list_nodes(
        node_type: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        current_user: AuthUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        """List graph nodes for lightweight inspection and debugging."""
        items = db.list_memory_nodes(current_user.id, node_type=node_type, limit=limit)
        return {"items": items, "total": len(items)}

    return router
