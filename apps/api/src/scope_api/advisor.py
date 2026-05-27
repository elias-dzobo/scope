"""Advisor harness built on memory retrieval and coverage gates.

The advisor harness is the product-facing agent loop boundary:
1. plan context needs
2. retrieve user memory
3. assess coverage
4. prepare research-gap requests when memory is weak
5. synthesize a guarded answer
6. write the turn back into user memory

This first implementation does not launch expensive deep research inline. It
creates the durable harness trace and the exact gap requests that the next
slice can feed into the Gemini-first deep research system.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from scope_api import db
from scope_api.auth.dependencies import require_current_user
from scope_api.auth.models import AuthUser
from scope_api.generic_research import GenericFinancialResearchHarness
from scope_api.memory import KNOWN_COMPANY_TICKERS, _short, _text, plan_user_context
from scope_api.agents.advisor import AdvisorAgent
from research_core.harness.controller import ResearchController

# When True, all advisor turns go through the agentic harness.
# Set ADVISOR_AGENT_ENABLED=false to fall back to the legacy pipeline.
_ADVISOR_AGENT_ENABLED = os.getenv("ADVISOR_AGENT_ENABLED", "true").lower() not in {"false", "0", "no"}


DEFAULT_RESEARCH_PILLARS = ["Macro & Industry", "Financial Engine", "Valuation"]
MAX_INLINE_RESEARCH_REQUESTS = 1
ADVISOR_SYNTHESIS_MODEL = os.getenv("ADVISOR_SYNTHESIS_MODEL", "gpt-4o-mini")
ADVISOR_RESEARCH_PLANNER_MODEL = os.getenv("ADVISOR_RESEARCH_PLANNER_MODEL", "gpt-4o-mini")

ADVISOR_SYNTHESIS_SYSTEM = """You are Scope's investment research advisor.

Answer conversationally using only the supplied context pack, evidence references, and fresh research results.
Rules:
- Synthesize the context; do not paste retrieved chunks or repeat a previous advisor answer.
- Use saved research as evidence, not as prose to copy.
- Explain financial terms naturally for a non-professional investor.
- Separate what the company/research says from what may fit the user profile.
- If context is thin, stale, or contaminated, say so and keep confidence low.
- Do not provide guarantees, certainty of returns, or direct personal financial instructions.
- Do not expose raw onboarding answers or internal pipeline/tool names.
- Keep the answer concise, but make it feel like a real conversation.

Return structured data matching the AdvisorAnswer schema."""

ADVISOR_SYNTHESIS_INSTRUCTION = """User question:
{query}

Context payload:
{payload}
"""

INCOMPLETE_ANSWER_PATTERN = re.compile(r"(\*\*\s*\d+\.?|\b\d+\.|[:;,]\s*)$")

ADVISOR_RESEARCH_PLANNER_SYSTEM = """You plan whether Scope's advisor should answer from thread context, saved memory, or hybrid saved+fresh research.

Prefer hybrid research when the user asks for current facts, external ties, recent developments, comparisons, company discovery, sector/theme analysis, government contracts, partnerships, valuation updates, or risks that may have changed.
Use thread state to resolve references like "they", "those companies", "this result", and "the companies listed".
Return structured data matching AdvisorResearchPlan."""


class AdvisorRunRequest(BaseModel):
    """Request to run one advisor harness turn."""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["auto", "company_research_question", "general_financial_research_question"] = "auto"
    conversation_id: str = Field(default="", alias="conversationId")
    allow_deep_research: bool = Field(
        default=False,
        alias="allowDeepResearch",
        description="Reserved for the next slice where research gaps can launch fresh research.",
    )


class AdvisorAnswer(BaseModel):
    """User-facing advisor response with traceable support metadata."""

    model_config = ConfigDict(populate_by_name=True)

    answer: str
    stance: str
    confidence: str
    key_points: list[str] = Field(default_factory=list, alias="keyPoints")
    personalization_notes: list[str] = Field(default_factory=list, alias="personalizationNotes")
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, alias="evidenceRefs")
    limitations: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list, alias="nextActions")
    advisor_synthesis_source: str = Field(default="", alias="advisorSynthesisSource")


class AdvisorRunResponse(BaseModel):
    """Public advisor run contract."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    conversation_id: str = Field(default="", alias="conversationId")
    status: str
    query: str
    mode: str
    answer: dict[str, Any]
    plan: dict[str, Any]
    coverage: dict[str, Any]
    research_requests: list[dict[str, Any]] = Field(default_factory=list, alias="researchRequests")
    context_pack: dict[str, Any] = Field(default_factory=dict, alias="contextPack")
    trace: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(alias="createdAt")
    completed_at: str = Field(default="", alias="completedAt")


class AdvisorMessageResponse(BaseModel):
    """Public advisor conversation message."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    conversation_id: str = Field(alias="conversationId")
    advisor_run_id: str = Field(default="", alias="advisorRunId")
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(alias="createdAt")


class AdvisorConversationResponse(BaseModel):
    """Public advisor conversation thread contract."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    active_topic: str = Field(default="", alias="activeTopic")
    active_research_run_id: str = Field(default="", alias="activeResearchRunId")
    active_generic_research_id: str = Field(default="", alias="activeGenericResearchId")
    active_entities: list[str] = Field(default_factory=list, alias="activeEntities")
    active_themes: list[str] = Field(default_factory=list, alias="activeThemes")
    status: str
    messages: list[AdvisorMessageResponse] = Field(default_factory=list)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class CreateAdvisorConversationRequest(BaseModel):
    """Request to create an advisor conversation, optionally seeded by research."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = ""
    active_research_run_id: str = Field(default="", alias="activeResearchRunId")
    active_generic_research_id: str = Field(default="", alias="activeGenericResearchId")
    active_topic: str = Field(default="", alias="activeTopic")
    active_entities: list[str] = Field(default_factory=list, alias="activeEntities")
    active_themes: list[str] = Field(default_factory=list, alias="activeThemes")


class AdvisorResearchPlan(BaseModel):
    """Decision contract for advisor memory/fresh-research orchestration."""

    model_config = ConfigDict(populate_by_name=True)

    answer_mode: Literal[
        "thread_only",
        "memory_only",
        "hybrid_research",
        "generic_research",
        "company_research",
        "clarify",
    ] = Field(alias="answerMode")
    resolved_question: str = Field(alias="resolvedQuestion")
    referenced_entities: list[str] = Field(default_factory=list, alias="referencedEntities")
    referenced_tickers: list[str] = Field(default_factory=list, alias="referencedTickers")
    referenced_themes: list[str] = Field(default_factory=list, alias="referencedThemes")
    memory_queries: list[str] = Field(default_factory=list, alias="memoryQueries")
    fresh_research_queries: list[str] = Field(default_factory=list, alias="freshResearchQueries")
    research_requests: list[dict[str, Any]] = Field(default_factory=list, alias="researchRequests")
    should_run_fresh_research: bool = Field(default=False, alias="shouldRunFreshResearch")
    force_generic_research: bool = Field(default=False, alias="forceGenericResearch")
    use_conversation_context: bool = Field(default=True, alias="useConversationContext")
    reasoning: str = ""
    planner_source: str = Field(default="deterministic_fallback", alias="plannerSource")


_LLM_ONLY_PATTERNS = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|sure|great|got it|sounds good|"
    r"what is|what are|what does|what do|explain|define|tell me about|how does|how do|"
    r"why is|why does|why are|can you|could you|please|what's|whats)\b",
    re.IGNORECASE,
)
_MEMORY_TRIGGERS = re.compile(
    r"\b(?:my|i|we|our|portfolio|profile|saved|previous|prior|last time|remember|"
    r"earlier|history|before|recommend for me|fit me|suits me|for my)\b",
    re.IGNORECASE,
)
_RESEARCH_TRIGGERS = re.compile(
    r"\b(?:research|analyse|analyze|report|deep dive|financials|earnings|revenue|"
    r"valuation|fundamentals|score|growth|margins|debt|balance sheet|cash flow|"
    r"sector|industry|market|compare|vs|versus|buy|sell|hold|thesis)\b",
    re.IGNORECASE,
)
_TICKER_RE = re.compile(r"\b[A-Z]{1,5}\b")
_TICKER_STOP = {"AI", "A", "I", "AM", "IN", "IS", "OR", "AT", "MY", "BE", "NO", "DO", "SO"}


def _quick_classify_query(query: str) -> str:
    """Deterministic upfront intent classification — no LLM required.

    Returns one of:
    - ``"llm_only"``      — conversational or definitional; skip memory entirely
    - ``"memory_search"`` — needs prior context but probably not fresh research
    - ``"needs_research"``— fresh grounded data likely required

    The classifier is intentionally conservative: when in doubt it returns
    ``"memory_search"`` so the full context pipeline still runs.  Only obvious
    definitional / greeting queries short-circuit to ``"llm_only"``.
    """
    q = query.strip()
    lower = q.lower()

    # Explicit research markers always win
    if _RESEARCH_TRIGGERS.search(q):
        return "needs_research"

    # Ticker symbols strongly imply company research
    tickers = [t for t in _TICKER_RE.findall(q) if t not in _TICKER_STOP and len(t) >= 2]
    if tickers:
        return "needs_research"

    # Known company names in the query
    for company in KNOWN_COMPANY_TICKERS:
        if company in lower:
            return "needs_research"

    # Personal/portfolio language → memory search
    if _MEMORY_TRIGGERS.search(q):
        return "memory_search"

    # Very short pure conversational opener → LLM only
    if _LLM_ONLY_PATTERNS.match(q) and len(q.split()) <= 10:
        return "llm_only"

    # Default: go through memory pipeline
    return "memory_search"


def _resolve_mode(mode: str, plan: dict[str, Any]) -> str:
    """Resolve auto mode into a stable advisor mode."""
    if mode != "auto":
        return mode
    if plan.get("intent") in {"industry_research", "comparison"} and not plan.get("tickers"):
        return "general_financial_research_question"
    if plan.get("intent") == "industry_research" and plan.get("themes"):
        return "general_financial_research_question"
    return "company_research_question"


def _public_advisor_message(row: dict[str, Any]) -> dict[str, Any]:
    """Return one API-facing advisor message."""
    return AdvisorMessageResponse(
        id=row["id"],
        conversationId=row["conversation_id"],
        advisorRunId=row.get("advisor_run_id", ""),
        role=row["role"],
        content=row["content"],
        metadata=row.get("metadata", {}),
        createdAt=row["created_at"],
    ).model_dump(mode="json", by_alias=True)


def _public_advisor_conversation(row: dict[str, Any], *, include_messages: bool = False) -> dict[str, Any]:
    """Return one API-facing advisor conversation."""
    messages = []
    if include_messages:
        messages = [_public_advisor_message(item) for item in db.list_advisor_messages(row["id"], row["user_id"])]
    return AdvisorConversationResponse(
        id=row["id"],
        title=row["title"],
        activeTopic=row["active_topic"],
        activeResearchRunId=row["active_research_run_id"],
        activeGenericResearchId=row["active_generic_research_id"],
        activeEntities=row["active_entities"],
        activeThemes=row["active_themes"],
        status=row["status"],
        messages=messages,
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    ).model_dump(mode="json", by_alias=True)


def _conversation_title(query: str) -> str:
    """Create a compact thread title from the opening user question."""
    return _short(query.strip().rstrip("?."), 80) or "Advisor conversation"


def _resolve_conversation(user_id: str, payload: AdvisorRunRequest) -> dict[str, Any]:
    """Load an existing conversation or create a new one for this turn."""
    conversation_id = payload.conversation_id.strip()
    if conversation_id:
        conversation = db.get_advisor_conversation(conversation_id, user_id)
        if not conversation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Advisor conversation not found")
        return conversation
    return db.create_advisor_conversation(user_id=user_id, title=_conversation_title(payload.query))


def _thread_context(conversation: dict[str, Any], messages: list[dict[str, Any]], limit: int = 8) -> dict[str, Any]:
    """Build compact thread state for planning and synthesis."""
    recent = messages[-limit:]
    return {
        "conversationId": conversation["id"],
        "activeTopic": conversation.get("active_topic", ""),
        "activeResearchRunId": conversation.get("active_research_run_id", ""),
        "activeGenericResearchId": conversation.get("active_generic_research_id", ""),
        "activeEntities": conversation.get("active_entities", []),
        "activeThemes": conversation.get("active_themes", []),
        "recentMessages": [
            {
                "role": item.get("role"),
                "content": _short(item.get("content", ""), 900),
                "metadata": item.get("metadata", {}),
            }
            for item in recent
        ],
    }


def _conversation_augmented_query(query: str, conversation: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    """Expand ambiguous follow-ups with thread state before memory retrieval."""
    lower = query.lower()
    markers = [
        "they",
        "those",
        "them",
        "listed",
        "this result",
        "that result",
        "the companies",
        "these companies",
        "break this",
        "follow up",
    ]
    has_anchor = bool(conversation.get("active_research_run_id") or conversation.get("active_generic_research_id"))
    if not has_anchor and not any(marker in lower for marker in markers):
        return query
    parts = [query]
    if conversation.get("active_topic"):
        parts.append(f"Current conversation topic: {conversation['active_topic']}.")
    if conversation.get("active_research_run_id"):
        parts.append(f"Active research run id: {conversation['active_research_run_id']}.")
    if conversation.get("active_generic_research_id"):
        parts.append(f"Active generic research id: {conversation['active_generic_research_id']}.")
    if conversation.get("active_themes"):
        parts.append(f"Active themes: {', '.join(conversation['active_themes'])}.")
    if conversation.get("active_entities"):
        parts.append(f"Active companies/entities: {', '.join(conversation['active_entities'])}.")
    previous_assistant = next((item for item in reversed(messages) if item.get("role") == "assistant"), None)
    if previous_assistant:
        parts.append(f"Previous assistant answer: {_short(previous_assistant.get('content', ''), 1200)}")
    return "\n\n".join(parts)


def _extract_conversation_state(
    *,
    query: str,
    plan: dict[str, Any],
    answer: dict[str, Any],
    targeted_research_results: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Extract durable thread state from one advisor turn."""
    entities: list[str] = []
    themes: list[str] = []
    conversation = dict((context.get("conversation") or {}))
    entities.extend(str(item) for item in conversation.get("activeEntities", []) if str(item).strip())
    themes.extend(str(item) for item in conversation.get("activeThemes", []) if str(item).strip())
    active_generic_research_id = str(conversation.get("activeGenericResearchId") or "")
    active_research_run_id = str(conversation.get("activeResearchRunId") or "")
    topic = str(conversation.get("activeTopic") or "").strip()
    if not topic:
        topic = str(plan.get("themes", [])[0] if plan.get("themes") else plan.get("intent") or "").replace("_", " ")
    for item in targeted_research_results:
        themes.extend(str(theme) for theme in item.get("themes", []) if str(theme).strip())
        active_generic_research_id = str(item.get("memoryNodeId") or active_generic_research_id)
        for key in ["directBeneficiaries", "secondOrderBeneficiaries"]:
            for company in list(item.get(key) or []):
                if isinstance(company, dict):
                    ticker = f" ({company.get('ticker')})" if company.get("ticker") else ""
                    entities.append(f"{company.get('name', '')}{ticker}".strip())
    for memo in _generic_results_from_context(context):
        themes.extend(str(theme) for theme in memo.get("themes", []) if str(theme).strip())
        for key in ["directBeneficiaries", "secondOrderBeneficiaries"]:
            for company in list(memo.get(key) or []):
                if isinstance(company, dict):
                    ticker = f" ({company.get('ticker')})" if company.get("ticker") else ""
                    entities.append(f"{company.get('name', '')}{ticker}".strip())
    for ref in answer.get("evidenceRefs", []):
        metadata = ref.get("metadata") or {}
        if metadata.get("ticker"):
            entities.append(str(metadata["ticker"]))
        if metadata.get("runId"):
            active_research_run_id = str(metadata["runId"])
    if not topic:
        topic = _short(query, 80)
    return {
        "active_topic": topic,
        "active_research_run_id": active_research_run_id,
        "active_generic_research_id": active_generic_research_id,
        "active_entities": _dedupe_strings(entities)[:24],
        "active_themes": _dedupe_strings(themes or plan.get("themes", []))[:12],
    }


def _dedupe_strings(values: list[Any]) -> list[str]:
    """Return non-empty strings in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        out.append(normalized)
    return out


def _extract_ticker_from_entity(value: str) -> str:
    """Extract a ticker from strings like 'NVIDIA (NVDA)'."""
    match = re.search(r"\(([A-Z][A-Z0-9.\-]{1,8})\)", value)
    if match:
        return match.group(1).upper()
    stripped = re.sub(r"[^A-Za-z0-9.\-]", "", value).upper()
    return stripped if 1 < len(stripped) <= 6 and stripped.isupper() else ""


def _is_research_like_query(query: str) -> bool:
    """Classify questions that benefit from fresh verification by default."""
    lower = query.lower()
    keywords = [
        "latest",
        "current",
        "recent",
        "today",
        "government",
        "contract",
        "ties",
        "partnership",
        "supplier",
        "customer",
        "companies",
        "stocks",
        "benefit",
        "compare",
        "versus",
        "vs",
        "risks",
        "catalyst",
        "outlook",
        "market",
        "industry",
        "sector",
        "positioned",
        "watch",
        "lookout",
        "poised",
        "relation",
        "exposure",
        "rely on",
    ]
    explanation_markers = ["explain", "summarize", "break down this report", "what does", "why did you"]
    return any(word in lower for word in keywords) and not any(marker in lower for marker in explanation_markers)


def _is_generic_or_comparative_question(
    *,
    query: str,
    entities: list[str],
    themes: list[str],
    plan: dict[str, Any],
) -> bool:
    """Return True when the advisor should run thematic research, not ticker-by-ticker company research."""
    lower = query.lower()
    comparative_markers = [
        "they",
        "those",
        "them",
        "listed",
        "the companies",
        "these companies",
        "which of",
        "compare",
        "supply chain",
        "industry",
        "sector",
        "market",
        "theme",
        "ties",
        "government",
        "contracts",
    ]
    if any(marker in lower for marker in comparative_markers):
        return True
    if themes and not plan.get("tickers"):
        return True
    return not plan.get("tickers") and plan.get("intent") in {"industry_research", "comparison"}


def _build_advisor_research_plan(
    *,
    query: str,
    augmented_query: str,
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    planned_context: dict[str, Any],
    allow_deep_research: bool,
) -> AdvisorResearchPlan:
    """Decide how to combine thread state, saved memory, and fresh research."""
    fallback = _build_advisor_research_plan_deterministic(
        query=query,
        augmented_query=augmented_query,
        conversation=conversation,
        planned_context=planned_context,
        allow_deep_research=allow_deep_research,
    )
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return fallback
    try:
        model = ChatOpenAI(model=ADVISOR_RESEARCH_PLANNER_MODEL, temperature=0)
        structured = model.with_structured_output(AdvisorResearchPlan, method="function_calling")
        parsed = structured.invoke(
            [
                ("system", ADVISOR_RESEARCH_PLANNER_SYSTEM),
                (
                    "human",
                    json.dumps(
                        {
                            "query": query,
                            "augmentedQuery": augmented_query,
                            "allowDeepResearch": allow_deep_research,
                            "conversation": _thread_context(conversation, messages),
                            "retrievalPlan": planned_context.get("plan", {}),
                            "coverage": planned_context.get("coverage", {}),
                            "deterministicFallback": fallback.model_dump(mode="json", by_alias=True),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        plan = parsed if isinstance(parsed, AdvisorResearchPlan) else AdvisorResearchPlan.model_validate(parsed)
        return _validate_advisor_research_plan(plan, fallback, allow_deep_research)
    except Exception:
        return fallback


def _build_advisor_research_plan_deterministic(
    *,
    query: str,
    augmented_query: str,
    conversation: dict[str, Any],
    planned_context: dict[str, Any],
    allow_deep_research: bool,
) -> AdvisorResearchPlan:
    """Deterministic fallback for advisor fresh-research decisions."""
    base_plan = planned_context.get("plan", {})
    coverage = planned_context.get("coverage", {})
    active_entities = _dedupe_strings(conversation.get("active_entities", []))
    active_themes = _dedupe_strings(conversation.get("active_themes", []))
    tickers = _dedupe_strings([*_text_list(base_plan.get("tickers")), *[_extract_ticker_from_entity(item) for item in active_entities]])
    entities = _dedupe_strings([*_text_list(base_plan.get("entities")), *active_entities])
    themes = _dedupe_strings([*_text_list(base_plan.get("themes")), *active_themes])
    research_like = _is_research_like_query(query)
    force_generic = _is_generic_or_comparative_question(
        query=query,
        entities=entities,
        themes=themes,
        plan=base_plan,
    )
    has_memory = bool((planned_context.get("context") or {}).get("chunks") or (planned_context.get("context") or {}).get("matchedNodes"))
    needs_fresh = bool(research_like or coverage.get("shouldRunDeepResearch"))
    answer_mode = "hybrid_research" if needs_fresh and has_memory else "generic_research" if needs_fresh else "memory_only" if has_memory else "clarify"
    if tickers and needs_fresh and not force_generic:
        answer_mode = "company_research"
    focus = ", ".join(entities or themes or tickers)
    resolved = augmented_query if augmented_query != query else query
    research_query = resolved
    if focus and focus.lower() not in research_query.lower():
        research_query = f"{query} Focus entities/themes: {focus}."
    request_mode = "generic_financial_research" if force_generic or answer_mode in {"hybrid_research", "generic_research"} else "company_research"
    research_requests = []
    if needs_fresh:
        research_requests.append(
            {
                "reason": "advisor_hybrid_research" if has_memory else "advisor_fresh_research",
                "mode": request_mode,
                "targets": entities or themes or tickers or [_short(query, 120)],
                "query": research_query,
                "preferredSourceTypes": ["primary_documents", "grounded_search", "recent_sources"],
            }
        )
    return AdvisorResearchPlan(
        answerMode=answer_mode,
        resolvedQuestion=resolved,
        referencedEntities=entities[:12],
        referencedTickers=tickers[:12],
        referencedThemes=themes[:8],
        memoryQueries=_dedupe_strings([augmented_query, query, *themes, *entities, *tickers])[:12],
        freshResearchQueries=_dedupe_strings([research_query, *[f"{item} {query}" for item in (entities or themes or tickers)[:6]]])[:12],
        researchRequests=research_requests,
        shouldRunFreshResearch=needs_fresh,
        forceGenericResearch=force_generic,
        useConversationContext=True,
        reasoning="Research-like advisor questions use saved memory plus fresh research; execution depends on allowDeepResearch.",
        plannerSource="deterministic_fallback",
    )


def _validate_advisor_research_plan(
    plan: AdvisorResearchPlan,
    fallback: AdvisorResearchPlan,
    allow_deep_research: bool,
) -> AdvisorResearchPlan:
    """Constrain the LLM advisor research plan before execution."""
    allowed_modes = {"thread_only", "memory_only", "hybrid_research", "generic_research", "company_research", "clarify"}
    answer_mode = plan.answer_mode if plan.answer_mode in allowed_modes else fallback.answer_mode
    should_fresh = bool(plan.should_run_fresh_research)
    requests = list(plan.research_requests or fallback.research_requests)[:MAX_INLINE_RESEARCH_REQUESTS]
    if should_fresh and not requests:
        requests = fallback.research_requests[:MAX_INLINE_RESEARCH_REQUESTS]
    for request in requests:
        request.setdefault("mode", "generic_financial_research" if answer_mode in {"hybrid_research", "generic_research"} else "company_research")
        request.setdefault("preferredSourceTypes", ["primary_documents", "grounded_search", "recent_sources"])
    return AdvisorResearchPlan(
        answerMode=answer_mode,
        resolvedQuestion=(plan.resolved_question or fallback.resolved_question).strip(),
        referencedEntities=_dedupe_strings(plan.referenced_entities or fallback.referenced_entities)[:12],
        referencedTickers=_dedupe_strings([ticker.upper() for ticker in (plan.referenced_tickers or fallback.referenced_tickers)])[:12],
        referencedThemes=_dedupe_strings(plan.referenced_themes or fallback.referenced_themes)[:8],
        memoryQueries=_dedupe_strings(plan.memory_queries or fallback.memory_queries)[:12],
        freshResearchQueries=_dedupe_strings(plan.fresh_research_queries or fallback.fresh_research_queries)[:12],
        researchRequests=requests,
        shouldRunFreshResearch=should_fresh,
        forceGenericResearch=plan.force_generic_research or fallback.force_generic_research,
        useConversationContext=plan.use_conversation_context,
        reasoning=plan.reasoning or fallback.reasoning,
        plannerSource="llm",
    )


def _text_list(value: Any) -> list[str]:
    """Normalize unknown list-like values into strings."""
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value:
        return [str(value)]
    return []


def _trace_step(name: str, status_value: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create one serializable advisor trace event."""
    return {
        "step": name,
        "status": status_value,
        "payload": payload or {},
        "createdAt": db.utc_now_iso(),
    }


def _evidence_refs(context: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    """Summarize retrieved chunks into lightweight evidence references."""
    refs: list[dict[str, Any]] = []
    for chunk in _research_chunks(context)[:limit]:
        refs.append(
            {
                "sourceType": chunk.get("source_type"),
                "sourceId": chunk.get("source_id"),
                "nodeTitle": chunk.get("node_title", ""),
                "metadata": chunk.get("metadata", {}),
                "excerpt": _short(chunk.get("text", ""), 260),
            }
        )
    return refs


def _research_chunks(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Return retrieved chunks that can be used as research evidence.

    Prior advisor answers are useful conversation history, but they are not
    primary evidence. Keeping them out of the evidence lane prevents recursive
    answers like "based on the previous answer to this same question...".
    """
    excluded = {"advisor_answer"}
    return [
        chunk
        for chunk in list(context.get("chunks") or [])
        if str(chunk.get("source_type") or "") not in excluded
    ]


def _conversation_chunks(context: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """Return recent advisor chunks as conversation history, not evidence."""
    return [
        chunk
        for chunk in list(context.get("chunks") or [])
        if str(chunk.get("source_type") or "") == "advisor_answer"
    ][:limit]


def _company_name_for_ticker(ticker: str) -> str:
    """Resolve a friendly company name for known tickers."""
    normalized = ticker.upper()
    for company, mapped_ticker in KNOWN_COMPANY_TICKERS.items():
        if mapped_ticker == normalized:
            return company.title()
    return normalized


def _pillars_for_research_request(request: dict[str, Any]) -> list[str]:
    """Choose a small pillar set for targeted advisor-triggered research."""
    reason = _text(request.get("reason")).lower()
    query = _text(request.get("query")).lower()
    joined = f"{reason} {query}"
    pillars: list[str] = []
    if any(word in joined for word in ["valuation", "multiple", "expensive", "cheap"]):
        pillars.append("Valuation")
    if any(word in joined for word in ["cash", "profit", "margin", "debt", "financial"]):
        pillars.append("Financial Engine")
    if any(word in joined for word in ["industry", "theme", "market", "ai infrastructure", "semiconductor"]):
        pillars.append("Macro & Industry")
    return pillars or list(DEFAULT_RESEARCH_PILLARS)


def _run_targeted_research(
    *,
    user_id: str,
    query: str,
    plan: dict[str, Any],
    context: dict[str, Any],
    research_requests: list[dict[str, Any]],
    controller: ResearchController | None = None,
    generic_harness: GenericFinancialResearchHarness | None = None,
) -> list[dict[str, Any]]:
    """Execute bounded deep research for advisor coverage gaps.

    The first production-safe slice only auto-runs company research when the
    planner has explicit tickers. General thematic research still produces
    executable requests, but it remains deferred until the generic research
    harness exists.
    """
    if not research_requests:
        return []
    controller = controller or ResearchController()
    profile = context.get("userProfile") or {}
    results: list[dict[str, Any]] = []
    tickers = list(plan.get("tickers") or [])
    advisor_plan = dict(plan.get("advisorResearchPlan") or {})
    force_generic = bool(advisor_plan.get("forceGenericResearch")) or any(
        str(request.get("mode") or "") == "generic_financial_research" for request in research_requests
    )
    if force_generic or not tickers:
        harness = generic_harness or GenericFinancialResearchHarness()
        generic_plan = dict(plan)
        if force_generic:
            generic_plan["tickers"] = []
        generic_result = harness.run(
            query=query,
            plan=generic_plan,
            research_requests=research_requests[:MAX_INLINE_RESEARCH_REQUESTS],
            user_profile=profile,
        )
        result = {
            "status": generic_result.get("status", "completed"),
            "mode": "generic_financial_research",
            "query": query,
            "request": research_requests[0],
            "themes": generic_result.get("themes", []),
            "synthesis": generic_result.get("synthesis", ""),
            "rawSynthesis": generic_result.get("rawSynthesis", ""),
            "keyFindings": generic_result.get("keyFindings", []),
            "layers": generic_result.get("layers", []),
            "directBeneficiaries": generic_result.get("directBeneficiaries", []),
            "secondOrderBeneficiaries": generic_result.get("secondOrderBeneficiaries", []),
            "risks": generic_result.get("risks", []),
            "valuationCaveats": generic_result.get("valuationCaveats", []),
            "sourceNotes": generic_result.get("sourceNotes", []),
            "synthesisSource": generic_result.get("synthesisSource", ""),
            "sources": generic_result.get("sources", []),
            "webSearchQueries": generic_result.get("webSearchQueries", []),
            "confidence": generic_result.get("confidence", "low"),
            "rawResult": generic_result,
        }
        result["memoryNodeId"] = _write_generic_research_memory(user_id=user_id, result=result)
        return [result]

    for request, ticker in zip(research_requests[:MAX_INLINE_RESEARCH_REQUESTS], tickers[:MAX_INLINE_RESEARCH_REQUESTS]):
        selected_pillars = _pillars_for_research_request(request)
        company_name = _company_name_for_ticker(ticker)
        summary = controller.run_company_research(
            company_name=company_name,
            ticker=ticker,
            selected_pillars=selected_pillars,
            user_financial_profile=profile.get("financialProfile"),
            user_risk_profile=profile.get("riskProfile"),
            investor_context=profile.get("investorContext"),
            user_id=user_id,
        )
        final_synthesis = dict(summary.get("final_synthesis") or {})
        scorecard = dict(summary.get("scorecard") or {})
        result = {
            "status": "completed",
            "ticker": ticker,
            "companyName": company_name,
            "selectedPillars": selected_pillars,
            "request": request,
            "overallScore": scorecard.get("overall_score"),
            "recommendation": scorecard.get("recommendation"),
            "investmentTakeaway": final_synthesis.get("investmentTakeaway", ""),
            "companySnapshot": final_synthesis.get("companySnapshot", ""),
            "mainRisks": final_synthesis.get("mainRisks", []),
            "sourceNote": final_synthesis.get("sourceNote", ""),
        }
        _write_targeted_research_memory(user_id=user_id, result=result)
        results.append(result)
    return results


def _profile_notes(context: dict[str, Any]) -> list[str]:
    """Turn derived user profile context into safe personalization notes."""
    profile = context.get("userProfile") or {}
    if not profile:
        return ["No onboarding profile was available, so the answer uses a conservative general-investor frame."]
    financial = profile.get("financialProfile") or {}
    risk = profile.get("riskProfile") or {}
    notes = []
    if risk:
        notes.append(
            "Risk fit used your derived profile: "
            f"tolerance={risk.get('riskTolerance', 'unknown')}, capacity={risk.get('riskCapacity', 'unknown')}."
        )
    if financial:
        notes.append(
            "Financial fit used your derived profile: "
            f"resilience={financial.get('financialResilience', 'unknown')}."
        )
    return notes or ["Your derived investor profile was available but did not contain enough detail for strong personalization."]


class AdvisorSynthesizer:
    """LLM-backed advisor synthesis over a GraphRAG context pack."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or ADVISOR_SYNTHESIS_MODEL

    def generate(
        self,
        *,
        query: str,
        mode: str,
        plan: dict[str, Any],
        context: dict[str, Any],
        coverage: dict[str, Any],
        refs: list[dict[str, Any]],
        targeted_research_results: list[dict[str, Any]],
        stance: str,
    ) -> AdvisorAnswer | None:
        """Generate a conversational answer, returning None when unavailable."""
        if not os.getenv("OPENAI_API_KEY", "").strip():
            return None
        payload = _advisor_synthesis_payload(
            mode=mode,
            plan=plan,
            context=context,
            coverage=coverage,
            refs=refs,
            targeted_research_results=targeted_research_results,
            stance=stance,
        )
        try:
            model = ChatOpenAI(model=self.model_name, temperature=0.2)
            structured = model.with_structured_output(AdvisorAnswer, method="function_calling")
            parsed = structured.invoke(
                [
                    ("system", ADVISOR_SYNTHESIS_SYSTEM),
                    (
                        "human",
                        ADVISOR_SYNTHESIS_INSTRUCTION.replace("{query}", query).replace(
                            "{payload}",
                            json.dumps(payload, ensure_ascii=False, indent=2),
                        ),
                    ),
                ]
            )
            if isinstance(parsed, AdvisorAnswer):
                out = parsed
            else:
                out = AdvisorAnswer.model_validate(parsed)
            out.evidence_refs = refs[:6]
            out.personalization_notes = out.personalization_notes or _profile_notes(context)
            out.stance = stance
            out.confidence = out.confidence or coverage.get("confidence", "low")
            out.advisor_synthesis_source = "llm"
            if _answer_looks_incomplete(out.answer, targeted_research_results):
                return None
            return out
        except Exception:
            return None


def _advisor_synthesis_payload(
    *,
    mode: str,
    plan: dict[str, Any],
    context: dict[str, Any],
    coverage: dict[str, Any],
    refs: list[dict[str, Any]],
    targeted_research_results: list[dict[str, Any]],
    stance: str,
) -> dict[str, Any]:
    """Build compact context for advisor LLM synthesis."""
    context_pack = dict(context.get("contextPack") or {})
    research_chunks = _research_chunks(context)
    context_pack["researchContext"] = research_chunks[:8]
    context_pack["genericResearchMemos"] = _generic_results_from_context(context)[:3]
    context_pack["sourceContext"] = [
        {
            "sourceType": ref.get("sourceType"),
            "nodeTitle": ref.get("nodeTitle"),
            "metadata": ref.get("metadata", {}),
            "excerpt": ref.get("excerpt", ""),
        }
        for ref in refs[:8]
    ]
    return {
        "mode": mode,
        "stance": stance,
        "plan": plan,
        "coverage": coverage,
        "contextPack": context_pack,
        "conversation": context.get("conversation", {}),
        "conversationHistoryForContinuityOnly": [
            {
                "nodeTitle": chunk.get("node_title", ""),
                "excerpt": _short(chunk.get("text", ""), 280),
            }
            for chunk in _conversation_chunks(context)
        ],
        "freshResearchResults": targeted_research_results[:3],
    }


def _fallback_answer_from_context(
    *,
    query: str,
    mode: str,
    plan: dict[str, Any],
    context: dict[str, Any],
    coverage: dict[str, Any],
    refs: list[dict[str, Any]],
    targeted_research_results: list[dict[str, Any]],
    stance: str,
) -> AdvisorAnswer:
    """Resilient non-LLM fallback used only when model synthesis is unavailable."""
    research_chunks = _research_chunks(context)
    completed_research = [item for item in targeted_research_results if item.get("status") in {"completed", "weak"}]
    saved_generic_research = _generic_results_from_context(context)
    key_points: list[str] = []
    if completed_research:
        for item in completed_research[:3]:
            if item.get("mode") == "generic_financial_research":
                key_points.extend([_short(point, 220) for point in item.get("keyFindings", [])[:3]])
            else:
                if item.get("companySnapshot"):
                    key_points.append(_short(f"{item.get('ticker')}: {item.get('companySnapshot')}", 220))
                if item.get("investmentTakeaway"):
                    key_points.append(_short(f"{item.get('ticker')} takeaway: {item.get('investmentTakeaway')}", 220))
                for risk in list(item.get("mainRisks") or [])[:2]:
                    key_points.append(_short(f"{item.get('ticker')} risk: {risk}", 220))
    else:
        for chunk in research_chunks[:4]:
            key_points.append(_short(chunk.get("text", ""), 220))

    if mode == "company_research_question" and plan.get("tickers"):
        prefix = f"For {', '.join(plan.get('tickers', []))}, "
    elif mode == "general_financial_research_question" and plan.get("themes"):
        prefix = f"On {', '.join(plan.get('themes', []))}, "
    else:
        prefix = ""

    generic_research = next(
        (item for item in completed_research if item.get("mode") == "generic_financial_research" and item.get("synthesis")),
        None,
    )
    if generic_research:
        answer = _generic_research_fallback_answer(query=query, result=generic_research)
    elif saved_generic_research:
        answer = _generic_memory_fallback_answer(query=query, result=saved_generic_research[0])
        key_points = saved_generic_research[0].get("keyFindings") or key_points
    elif key_points:
        answer = (
            f"{prefix}I found relevant saved research. Here is the useful part:\n\n"
            + "\n".join(f"- {point}" for point in key_points[:5])
        )
    else:
        answer = (
            "I could not find enough research evidence to answer this well yet. "
            "This should be treated as a new research task before relying on it."
        )
        stance = "no_relevant_memory"

    limitations = []
    if not os.getenv("OPENAI_API_KEY", "").strip():
        limitations.append("Conversational synthesis is unavailable because the advisor LLM is not configured.")
    if coverage.get("status") != "sufficient":
        limitations.append("Saved context is incomplete or stale, so confidence is limited.")

    return AdvisorAnswer(
        answer=answer,
        stance=stance,
        confidence=coverage.get("confidence", "low"),
        keyPoints=key_points[:5],
        personalizationNotes=_profile_notes(context),
        evidenceRefs=refs,
        limitations=limitations,
        nextActions=["Review the cited saved research before acting.", "Refresh research if the decision depends on recent market data."],
        advisorSynthesisSource="deterministic_fallback",
    )


def _generic_results_from_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract structured generic research memos from retrieved memory."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in list(context.get("matchedNodes") or []):
        if node.get("node_type") != "GenericResearchResult":
            continue
        properties = dict(node.get("properties") or {})
        key = str(node.get("id") or properties.get("query") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        properties.setdefault("query", node.get("title", ""))
        properties.setdefault("synthesis", node.get("summary", ""))
        results.append(properties)

    # Lexical retrieval can find the chunk without matching the node. Keep a
    # chunk-derived fallback so follow-up questions still get a coherent answer.
    for chunk in list(context.get("chunks") or []):
        if chunk.get("source_type") != "generic_research_result":
            continue
        key = str(chunk.get("node_id") or chunk.get("source_id") or "")
        if key in seen:
            continue
        seen.add(key)
        text = str(chunk.get("text") or "")
        metadata = dict(chunk.get("metadata") or {})
        results.append(
            {
                "query": chunk.get("node_title") or "Saved generic research",
                "synthesis": text,
                "keyFindings": _chunk_key_points(text),
                "themes": metadata.get("themes", []),
                "confidence": metadata.get("confidence", "low"),
            }
        )
    return results


def _chunk_key_points(text: str, limit: int = 5) -> list[str]:
    """Extract readable bullets from a saved chunk without duplicating the query."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<!\d)\.(?!\d)\s+", cleaned)
    points: list[str] = []
    for sentence in sentences:
        normalized = sentence.strip(" -*")
        if len(normalized) < 50:
            continue
        if "looking at the entire" in normalized.lower()[:120]:
            continue
        if not normalized.endswith((".", "?", "!")):
            normalized = f"{normalized}."
        points.append(normalized)
        if len(points) >= limit:
            break
    return points


def _generic_memory_fallback_answer(*, query: str, result: dict[str, Any]) -> str:
    """Build a conversational answer from saved generic research memory."""
    synthesis = str(result.get("synthesis") or "").strip()
    layers = [layer for layer in list(result.get("layers") or []) if isinstance(layer, dict)]
    direct = [item for item in list(result.get("directBeneficiaries") or []) if isinstance(item, dict)]
    second_order = [item for item in list(result.get("secondOrderBeneficiaries") or []) if isinstance(item, dict)]
    key_findings = [str(item).strip() for item in list(result.get("keyFindings") or []) if str(item).strip()]
    risks = [str(item).strip() for item in list(result.get("risks") or []) if str(item).strip()]
    caveats = [str(item).strip() for item in list(result.get("valuationCaveats") or []) if str(item).strip()]

    lines = [
        "Yes. The way I’d break this down is by asking: which companies are closest to unavoidable AI infrastructure spend?",
        "",
    ]
    if layers:
        lines.append("**Where to look**")
        for layer in layers[:6]:
            companies = _company_names(layer.get("companies") or [])
            suffix = f" Watch: {companies}." if companies else ""
            lines.append(f"- **{layer.get('name', 'Theme layer')}**: {_short(str(layer.get('explanation') or ''), 260)}{suffix}")
    elif key_findings:
        lines.append("**Main read**")
        lines.extend(f"- {point}" for point in key_findings[:5])
    elif synthesis:
        lines.append(_trim_to_complete_sentence(synthesis, max_chars=1800))

    if direct:
        lines.extend(["", "**Companies to keep on your radar first**"])
        for item in direct[:8]:
            ticker = f" ({item.get('ticker')})" if item.get("ticker") else ""
            lines.append(f"- **{item.get('name', 'Company')}{ticker}**: {_short(str(item.get('reason') or ''), 240)}")
    if second_order:
        lines.extend(["", "**Second-order beneficiaries**"])
        for item in second_order[:6]:
            ticker = f" ({item.get('ticker')})" if item.get("ticker") else ""
            lines.append(f"- **{item.get('name', 'Company')}{ticker}**: {_short(str(item.get('reason') or ''), 220)}")
    if risks or caveats:
        lines.extend(["", "**What to be careful about**"])
        lines.extend(f"- {item}" for item in [*risks[:3], *caveats[:2]])
    lines.extend(
        [
            "",
            "I’d treat this as a watchlist, not a buy list. The next step is company-specific research on valuation, margins, durability of demand, and whether the AI upside is already priced in.",
        ]
    )
    return "\n".join(line for line in lines if line is not None)


def _company_names(items: list[Any], limit: int = 6) -> str:
    """Format company names from structured generic research output."""
    names: list[str] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        ticker = f" ({item.get('ticker')})" if item.get("ticker") else ""
        names.append(f"{item.get('name', 'Company')}{ticker}")
    return ", ".join(names)


def _answer_looks_incomplete(answer: str, targeted_research_results: list[dict[str, Any]]) -> bool:
    """Detect model answers that ended mid-list or failed to use rich fresh research."""
    stripped = answer.strip()
    if not stripped:
        return True
    if INCOMPLETE_ANSWER_PATTERN.search(stripped):
        return True
    if stripped.count("**") % 2 == 1:
        return True
    has_long_generic_research = any(
        item.get("mode") == "generic_financial_research" and len(str(item.get("synthesis") or "")) > 1500
        for item in targeted_research_results
    )
    return has_long_generic_research and len(stripped) < 1200


def _generic_research_fallback_answer(*, query: str, result: dict[str, Any]) -> str:
    """Return a complete generic-research answer when LLM synthesis is unavailable."""
    synthesis = str(result.get("synthesis") or "").strip()
    if not synthesis:
        findings = [str(item).strip() for item in result.get("keyFindings", []) if str(item).strip()]
        synthesis = "\n\n".join(findings)
    if not synthesis:
        return (
            "I ran fresh research, but the result did not contain enough usable synthesis to answer this well. "
            "The safest next step is to rerun the research with a narrower question."
        )
    intro = (
        f"I ran fresh research for: {query}\n\n"
        "Here is the clearest read from that research:\n\n"
    )
    body = _trim_to_complete_sentence(synthesis, max_chars=5200)
    return f"{intro}{body}"


def _trim_to_complete_sentence(text: str, *, max_chars: int) -> str:
    """Trim long text without leaving the answer mid-sentence or mid-list marker."""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    window = cleaned[:max_chars].rstrip()
    cut_points = [window.rfind(marker) for marker in ("\n\n", ". ", "? ", "! ")]
    cut = max(cut_points)
    if cut > max_chars * 0.65:
        return window[: cut + 1].rstrip()
    return window.rstrip(" ,;:-*") + "."


def _synthesize_answer(
    *,
    query: str,
    mode: str,
    planned_context: dict[str, Any],
    allow_deep_research: bool,
    targeted_research_results: list[dict[str, Any]] | None = None,
) -> AdvisorAnswer:
    """Synthesize a guarded advisor answer from retrieved memory and coverage."""
    plan = planned_context["plan"]
    context = planned_context["context"]
    coverage = planned_context["coverage"]
    refs = _evidence_refs(context)
    chunks = _research_chunks(context)
    research_requests = coverage.get("targetedResearchRequests") or []
    targeted_research_results = targeted_research_results or []

    completed_research = [item for item in targeted_research_results if item.get("status") in {"completed", "weak"}]
    for item in completed_research:
        for source in list(item.get("sources") or [])[:3]:
            refs.append(
                {
                    "sourceType": "fresh_research",
                    "sourceId": source.get("url") or source.get("link") or source.get("title", ""),
                    "nodeTitle": source.get("title", "Fresh research source"),
                    "metadata": {"mode": item.get("mode", "company_research")},
                    "excerpt": _short(item.get("synthesis") or item.get("investmentTakeaway") or item.get("companySnapshot") or "", 260),
                }
            )

    if completed_research:
        stance = "answered_with_fresh_research"
        synthesized = AdvisorSynthesizer().generate(
            query=query,
            mode=mode,
            plan=plan,
            context=context,
            coverage=coverage,
            refs=refs,
            targeted_research_results=targeted_research_results,
            stance=stance,
        )
        if synthesized:
            return synthesized
        return _fallback_answer_from_context(
            query=query,
            mode=mode,
            plan=plan,
            context=context,
            coverage=coverage,
            refs=refs,
            targeted_research_results=targeted_research_results,
            stance=stance,
        )
    if chunks:
        stance = "answered_from_memory"
        synthesized = AdvisorSynthesizer().generate(
            query=query,
            mode=mode,
            plan=plan,
            context=context,
            coverage=coverage,
            refs=refs,
            targeted_research_results=targeted_research_results,
            stance=stance,
        )
        if synthesized:
            return synthesized
        return _fallback_answer_from_context(
            query=query,
            mode=mode,
            plan=plan,
            context=context,
            coverage=coverage,
            refs=refs,
            targeted_research_results=targeted_research_results,
            stance=stance,
        )
    if coverage.get("shouldRunDeepResearch") and not allow_deep_research:
        answer = (
            "I do not have enough saved research to answer this confidently yet. "
            "The next step is targeted deep research for the missing areas before making a stronger assessment."
        )
        stance = "needs_research"
        limitations = [
            "Saved memory did not cover every required context category.",
            "No fresh external research was launched in this advisor pass.",
        ]
        next_actions = [
            f"Run targeted research: {item.get('query', query)}" for item in research_requests[:3]
        ] or ["Run targeted research for the missing context."]
        return AdvisorAnswer(
            answer=answer,
            stance=stance,
            confidence=coverage.get("confidence", "low"),
            keyPoints=[],
            personalizationNotes=_profile_notes(context),
            evidenceRefs=refs,
            limitations=limitations,
            nextActions=next_actions,
            advisorSynthesisSource="deterministic_fallback",
        )

    answer = (
        "I could not find relevant saved research for this question. "
        "This should be treated as a new research task rather than an advisor answer."
    )
    stance = "no_relevant_memory"
    limitations = ["No relevant saved research evidence was found."]
    next_actions = ["Run a new deep research workflow for this question."]

    return AdvisorAnswer(
        answer=answer,
        stance=stance,
        confidence=coverage.get("confidence", "low"),
        keyPoints=[],
        personalizationNotes=_profile_notes(context),
        evidenceRefs=refs,
        limitations=limitations,
        nextActions=next_actions,
        advisorSynthesisSource="deterministic_fallback",
    )


def _write_targeted_research_memory(*, user_id: str, result: dict[str, Any]) -> None:
    """Save advisor-triggered research as memory without creating a research_run row."""
    ticker = str(result.get("ticker") or "").upper()
    source_ref = {
        "sourceType": "advisor_targeted_research",
        "ticker": ticker,
        "query": result.get("request", {}).get("query", ""),
    }
    node = db.upsert_memory_node(
        user_id=user_id,
        node_type="TargetedResearchResult",
        external_id=f"{ticker}:{db.utc_now_iso()}",
        title=f"{ticker} targeted research",
        summary=_short(result.get("investmentTakeaway") or result.get("companySnapshot") or ""),
        properties=result,
        source_ref=source_ref,
    )
    if ticker:
        company = db.upsert_memory_node(
            user_id=user_id,
            node_type="Company",
            external_id=ticker,
            title=f"{result.get('companyName') or ticker} ({ticker})",
            summary=_short(result.get("companySnapshot", "")),
            properties={"ticker": ticker, "companyName": result.get("companyName") or ticker},
            source_ref=source_ref,
        )
        db.upsert_memory_edge(
            user_id=user_id,
            source_node_id=node["id"],
            target_node_id=company["id"],
            edge_type="ANALYZED",
            source_ref=source_ref,
        )
    db.create_memory_chunk(
        user_id=user_id,
        node_id=node["id"],
        source_type="targeted_research_result",
        source_id=node["id"],
        text=" ".join(
            [
                result.get("companySnapshot", ""),
                result.get("investmentTakeaway", ""),
                _text(result.get("mainRisks")),
                result.get("sourceNote", ""),
            ]
        ),
        metadata={"ticker": ticker, "selectedPillars": result.get("selectedPillars", [])},
    )


def _write_generic_research_memory(*, user_id: str, result: dict[str, Any]) -> str:
    """Save generic grounded research as thematic memory."""
    themes = [str(theme) for theme in result.get("themes", [])]
    external_id = f"generic:{db.utc_now_iso()}"
    source_ref = {"sourceType": "advisor_generic_research", "query": result.get("query", "")}
    node = db.upsert_memory_node(
        user_id=user_id,
        node_type="GenericResearchResult",
        external_id=external_id,
        title=_short(result.get("query", "Generic research"), 140),
        summary=_short(result.get("synthesis", ""), 900),
        properties=result,
        source_ref=source_ref,
    )
    for theme in themes:
        theme_node = db.upsert_memory_node(
            user_id=user_id,
            node_type="Theme",
            external_id=theme.lower(),
            title=theme,
            summary=f"Theme researched by advisor: {theme}",
            properties={"theme": theme},
            source_ref=source_ref,
        )
        db.upsert_memory_edge(
            user_id=user_id,
            source_node_id=node["id"],
            target_node_id=theme_node["id"],
            edge_type="RELATED_TO",
            source_ref=source_ref,
        )
        if "semiconductor" in theme.lower() or "industry" in theme.lower():
            industry_node = db.upsert_memory_node(
                user_id=user_id,
                node_type="Industry",
                external_id=theme.lower(),
                title=theme,
                summary=f"Industry context researched by advisor: {theme}",
                properties={"industry": theme},
                source_ref=source_ref,
            )
            db.upsert_memory_edge(
                user_id=user_id,
                source_node_id=node["id"],
                target_node_id=industry_node["id"],
                edge_type="RELATED_TO",
                source_ref=source_ref,
            )
    db.create_memory_chunk(
        user_id=user_id,
        node_id=node["id"],
        source_type="generic_research_result",
        source_id=node["id"],
        text=" ".join([result.get("query", ""), result.get("synthesis", ""), _text(result.get("keyFindings"))]),
        metadata={"themes": themes, "confidence": result.get("confidence", "low")},
    )
    return node["id"]


def _write_advisor_memory(
    *,
    user_id: str,
    advisor_run_id: str,
    query: str,
    mode: str,
    answer: dict[str, Any],
    plan: dict[str, Any],
    coverage: dict[str, Any],
) -> None:
    """Persist advisor turn output back into user memory."""
    source_ref = {"sourceType": "advisor_run", "advisorRunId": advisor_run_id}
    user_node = db.upsert_memory_node(
        user_id=user_id,
        node_type="User",
        external_id=user_id,
        title="User profile",
        summary="Advisor interaction owner.",
        properties={"hasAdvisorHistory": True},
        source_ref=source_ref,
    )
    advisor_node = db.upsert_memory_node(
        user_id=user_id,
        node_type="AdvisorRun",
        external_id=advisor_run_id,
        title=_short(query, 120),
        summary=_short(answer.get("answer", ""), 700),
        properties={
            "query": query,
            "mode": mode,
            "stance": answer.get("stance"),
            "confidence": answer.get("confidence"),
            "plan": plan,
            "coverage": coverage,
        },
        source_ref=source_ref,
    )
    db.upsert_memory_edge(
        user_id=user_id,
        source_node_id=user_node["id"],
        target_node_id=advisor_node["id"],
        edge_type="RELATED_TO",
        source_ref=source_ref,
    )
    db.create_memory_chunk(
        user_id=user_id,
        node_id=advisor_node["id"],
        source_type="advisor_answer",
        source_id=advisor_run_id,
        text=" ".join([query, answer.get("answer", ""), _text(answer.get("keyPoints")), _text(answer.get("limitations"))]),
        metadata={"mode": mode, "stance": answer.get("stance"), "confidence": answer.get("confidence")},
    )


def run_advisor_harness(
    *,
    user_id: str,
    payload: AdvisorRunRequest,
    research_controller: ResearchController | None = None,
    generic_harness: GenericFinancialResearchHarness | None = None,
) -> dict[str, Any]:
    """Execute one advisor harness turn and persist its trace."""
    conversation = _resolve_conversation(user_id, payload)
    existing_messages = db.list_advisor_messages(conversation["id"], user_id)
    augmented_query = _conversation_augmented_query(payload.query.strip(), conversation, existing_messages)
    run = db.create_advisor_run(
        user_id=user_id,
        query=payload.query.strip(),
        mode=payload.mode,
        conversation_id=conversation["id"],
    )
    db.create_advisor_message(
        conversation_id=conversation["id"],
        user_id=user_id,
        role="user",
        content=payload.query.strip(),
        advisor_run_id=run["id"],
        metadata={"augmentedQuery": augmented_query if augmented_query != payload.query.strip() else ""},
    )
    trace = [_trace_step("create_run", "completed", {"runId": run["id"]})]
    try:
        query_class = _quick_classify_query(payload.query.strip())
        if query_class == "llm_only":
            # Fast path: skip memory pipeline entirely for conversational/definitional queries.
            # Build a minimal planned_context so downstream code has the same shape.
            planned_context = {
                "plan": {
                    "intent": "clarification",
                    "tickers": [],
                    "themes": [],
                    "entities": [],
                    "originalQuery": payload.query.strip(),
                    "neededContext": [],
                    "advisorResearchPlan": {},
                },
                "context": {
                    "conversation": _thread_context(conversation, existing_messages),
                    "contextPack": {"chunks": [], "nodes": [], "missingContext": []},
                    "userProfile": {},
                    "coverage": {},
                    "advisorResearchPlan": {},
                },
                "coverage": {
                    "status": "sufficient",
                    "shouldRunDeepResearch": False,
                    "targetedResearchRequests": [],
                    "advisorResearchPlan": {},
                    "advisorWantsFreshResearch": False,
                },
            }
        else:
            planned_context = plan_user_context(user_id, query=augmented_query, limit=10)
        planned_context["context"]["conversation"] = _thread_context(conversation, existing_messages)
        planned_context["plan"]["originalQuery"] = payload.query.strip()
        plan = planned_context["plan"]
        mode = _resolve_mode(payload.mode, plan)
        coverage = planned_context["coverage"]
        advisor_research_plan = _build_advisor_research_plan(
            query=payload.query.strip(),
            augmented_query=augmented_query,
            conversation=conversation,
            messages=existing_messages,
            planned_context=planned_context,
            allow_deep_research=payload.allow_deep_research,
        )
        advisor_research_plan_payload = advisor_research_plan.model_dump(mode="json", by_alias=True)
        plan["advisorResearchPlan"] = advisor_research_plan_payload
        planned_context["context"]["advisorResearchPlan"] = advisor_research_plan_payload
        research_requests = list(advisor_research_plan.research_requests or coverage.get("targetedResearchRequests") or [])
        coverage["targetedResearchRequests"] = research_requests
        coverage["advisorResearchPlan"] = advisor_research_plan_payload
        coverage["advisorWantsFreshResearch"] = bool(advisor_research_plan.should_run_fresh_research)
        trace.extend(
            [
                _trace_step(
                    "plan_context",
                    "completed",
                    {
                        "intent": plan.get("intent"),
                        "mode": mode,
                        "queryClass": query_class,
                        "memorySkipped": query_class == "llm_only",
                        "conversationId": conversation["id"],
                        "threadAugmented": augmented_query != payload.query.strip(),
                    },
                ),
                _trace_step(
                    "retrieve_memory",
                    "skipped" if query_class == "llm_only" else "completed",
                    planned_context["context"].get("coverage", {}),
                ),
                _trace_step(
                    "coverage_gate",
                    "completed",
                    {
                        "status": coverage.get("status"),
                        "shouldRunDeepResearch": coverage.get("shouldRunDeepResearch"),
                    },
                ),
                _trace_step(
                    "advisor_research_planner",
                    "completed",
                    {
                        "answerMode": advisor_research_plan.answer_mode,
                        "shouldRunFreshResearch": advisor_research_plan.should_run_fresh_research,
                        "forceGenericResearch": advisor_research_plan.force_generic_research,
                        "requestCount": len(research_requests),
                        "plannerSource": advisor_research_plan.planner_source,
                    },
                ),
            ]
        )
        if research_requests and advisor_research_plan.should_run_fresh_research:
            if payload.allow_deep_research:
                targeted_research_results = _run_targeted_research(
                    user_id=user_id,
                    query=advisor_research_plan.resolved_question or payload.query.strip(),
                    plan=plan,
                    context=planned_context["context"],
                    research_requests=research_requests,
                    controller=research_controller,
                    generic_harness=generic_harness,
                )
                completed_count = len([item for item in targeted_research_results if item.get("status") == "completed"])
                trace.append(
                    _trace_step(
                        "research_gap_executor",
                        "completed" if completed_count else "deferred",
                        {
                            "requestCount": len(research_requests),
                            "completedCount": completed_count,
                            "allowDeepResearch": True,
                        },
                    )
                )
            else:
                targeted_research_results = []
                trace.append(
                    _trace_step(
                        "research_gap_executor",
                        "deferred",
                        {"requestCount": len(research_requests), "allowDeepResearch": False},
                    )
                )
        else:
            targeted_research_results = []
        answer_model = _synthesize_answer(
            query=payload.query.strip(),
            mode=mode,
            planned_context=planned_context,
            allow_deep_research=payload.allow_deep_research,
            targeted_research_results=targeted_research_results,
        )
        answer = answer_model.model_dump(mode="json", by_alias=True)
        if targeted_research_results:
            answer["targetedResearchResults"] = targeted_research_results
        answer["contextPack"] = planned_context["context"].get("contextPack", {})
        trace.append(_trace_step("advisor_synthesis", "completed", {"stance": answer["stance"]}))
        db.update_advisor_run(
            run["id"],
            status="completed",
            plan=plan,
            context=planned_context["context"],
            coverage=coverage,
            research_requests=research_requests,
            answer=answer,
            trace=trace,
            completed=True,
        )
        db.create_advisor_message(
            conversation_id=conversation["id"],
            user_id=user_id,
            role="assistant",
            content=answer.get("answer", ""),
            advisor_run_id=run["id"],
            metadata={
                "stance": answer.get("stance"),
                "confidence": answer.get("confidence"),
                "evidenceRefs": answer.get("evidenceRefs", []),
                "activeState": _extract_conversation_state(
                    query=payload.query.strip(),
                    plan=plan,
                    answer=answer,
                    targeted_research_results=targeted_research_results,
                    context=planned_context["context"],
                ),
            },
        )
        state = _extract_conversation_state(
            query=payload.query.strip(),
            plan=plan,
            answer=answer,
            targeted_research_results=targeted_research_results,
            context=planned_context["context"],
        )
        db.update_advisor_conversation(
            conversation["id"],
            user_id=user_id,
            title=conversation["title"] or _conversation_title(payload.query),
            **state,
        )
        _write_advisor_memory(
            user_id=user_id,
            advisor_run_id=run["id"],
            query=payload.query.strip(),
            mode=mode,
            answer=answer,
            plan=plan,
            coverage=coverage,
        )
        saved = db.get_advisor_run(run["id"], user_id)
        return _public_advisor_run(saved)
    except Exception as exc:
        trace.append(_trace_step("advisor_harness", "failed", {"error": str(exc)}))
        db.update_advisor_run(run["id"], status="failed", trace=trace, error_message=str(exc), completed=True)
        raise


def _public_advisor_run(row: dict[str, Any]) -> dict[str, Any]:
    """Return the API-facing advisor run shape."""
    return AdvisorRunResponse(
        id=row["id"],
        conversationId=row.get("conversation_id", ""),
        status=row["status"],
        query=row["query"],
        mode=row["mode"],
        answer=row["answer"],
        plan=row["plan"],
        coverage=row["coverage"],
        researchRequests=row["research_requests"],
        contextPack=(row.get("context") or {}).get("contextPack", {}),
        trace=row["trace"],
        createdAt=row["created_at"],
        completedAt=row["completed_at"],
    ).model_dump(mode="json", by_alias=True)


_advisor_agent: AdvisorAgent | None = None


def _get_advisor_agent() -> AdvisorAgent:
    """Return a singleton AdvisorAgent — compiled once, reused across requests."""
    global _advisor_agent
    if _advisor_agent is None:
        _advisor_agent = AdvisorAgent()
    return _advisor_agent


def run_advisor_agent_turn(
    *,
    user_id: str,
    payload: "AdvisorRunRequest",
) -> dict[str, Any]:
    """Execute one advisor turn using the agentic harness and persist the result.

    This replaces ``run_advisor_harness`` for all production traffic when
    ``ADVISOR_AGENT_ENABLED=true`` (the default).  The API contract is
    identical — callers receive the same ``AdvisorRunResponse`` shape.
    """
    conversation = _resolve_conversation(user_id, payload)
    existing_messages = db.list_advisor_messages(conversation["id"], user_id)
    # Augment the query with conversation context (active run, entities, etc.)
    # so the agent's context window contains the seed data without a tool call.
    augmented_query = _conversation_augmented_query(payload.query.strip(), conversation, existing_messages)

    # Persist the user turn immediately
    run = db.create_advisor_run(
        user_id=user_id,
        query=payload.query.strip(),
        mode=payload.mode,
        conversation_id=conversation["id"],
    )
    db.create_advisor_message(
        conversation_id=conversation["id"],
        user_id=user_id,
        role="user",
        content=payload.query.strip(),
        advisor_run_id=run["id"],
    )

    try:
        # Pass the already-fetched conversation and messages directly to the agent
        # so the context loader (context.py) can inject research results without
        # an extra round-trip to the database.
        result = _get_advisor_agent().run(
            user_id=user_id,
            query=augmented_query,
            conversation_id=conversation["id"],
            conversation=conversation,
            messages=existing_messages,
            allow_deep_research=payload.allow_deep_research,
            run_id=run["id"],
        )

        # Persist a minimal plan dict that preserves backward-compatible fields
        # (tests and the frontend read plan["query"] and plan["intent"])
        persisted_plan: dict[str, Any] = {
            "query": augmented_query,
            "originalQuery": payload.query.strip(),
            "intent": "agent",
            "tickers": [],
            "themes": [],
            "entities": [],
        }

        # Pull typed fields from the structured synthesis step in agent.py.
        # These are no longer hardcoded — the agent's post-ReAct extraction pass
        # produces stance, confidence, keyPoints, limitations, and nextActions.
        structured = result.metadata.get("structuredAnswer") or {}

        # Map AgentResult → the DB/response schema the rest of the app expects
        answer_payload: dict[str, Any] = {
            "answer": result.answer,
            "stance": structured.get("stance") or "neutral",
            "confidence": structured.get("confidence") or "medium",
            "keyPoints": structured.get("keyPoints") or [],
            "limitations": structured.get("limitations") or [],
            "nextActions": structured.get("nextActions") or [],
            "evidenceRefs": [],
            "targetedResearchResults": [],
            "contextPack": {},
            "agentTrace": [step.model_dump(mode="json") for step in result.trace],
            "agentToolCalls": [tc.model_dump(mode="json") for tc in result.tool_calls],
            "terminationReason": result.termination_reason,
            "warnings": result.warnings,
            "tokenUsage": result.usage.model_dump(),
        }
        trace = [
            _trace_step(step.name, step.status, step.data)
            for step in result.trace
            if step.type in {"system", "guardrail", "warning"}
        ]

        db.update_advisor_run(
            run["id"],
            status="completed",
            plan=persisted_plan,
            context={},
            coverage={},
            research_requests=[],
            answer=answer_payload,
            trace=trace,
            completed=True,
        )
        db.create_advisor_message(
            conversation_id=conversation["id"],
            user_id=user_id,
            role="assistant",
            content=result.answer,
            advisor_run_id=run["id"],
            metadata={
                "terminationReason": result.termination_reason,
                "toolCallCount": len(result.tool_calls),
                "tokenUsage": result.usage.model_dump(),
                "warnings": result.warnings,
            },
        )

        # Update conversation active state from the agent's context state
        agent_state = result.metadata.get("agentState") or {}
        db.update_advisor_conversation(
            conversation["id"],
            user_id=user_id,
            title=conversation["title"] or _conversation_title(payload.query),
            active_topic=agent_state.get("activeTopic", conversation.get("active_topic") or ""),
            active_research_run_id=agent_state.get("activeResearchRunId", conversation.get("active_research_run_id") or ""),
            active_generic_research_id=agent_state.get("activeGenericResearchId", conversation.get("active_generic_research_id") or ""),
            active_entities=agent_state.get("activeEntities", conversation.get("active_entities") or []),
            active_themes=agent_state.get("activeThemes", conversation.get("active_themes") or []),
        )

        # Write the turn into the memory graph for future retrieval
        _write_advisor_memory(
            user_id=user_id,
            advisor_run_id=run["id"],
            query=payload.query.strip(),
            mode=payload.mode,
            answer=answer_payload,
            plan=persisted_plan,
            coverage={},
        )

        saved = db.get_advisor_run(run["id"], user_id)
        return _public_advisor_run(saved)

    except Exception as exc:
        db.update_advisor_run(
            run["id"],
            status="failed",
            trace=[_trace_step("advisor_agent", "failed", {"error": str(exc)})],
            error_message=str(exc),
            completed=True,
        )
        raise


def build_advisor_router(api_prefix: str) -> APIRouter:
    """Build advisor harness routes under the versioned API prefix."""
    router = APIRouter(prefix=f"{api_prefix}/advisor", tags=["advisor"])

    @router.get("/conversations")
    async def list_conversations(
        limit: int = Query(default=20, ge=1, le=100),
        current_user: AuthUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        """List recent advisor conversation threads."""
        items = [
            _public_advisor_conversation(row, include_messages=False)
            for row in db.list_advisor_conversations(current_user.id, limit=limit)
        ]
        return {"items": items, "total": len(items)}

    @router.post("/conversations", response_model=AdvisorConversationResponse)
    async def create_conversation(
        payload: CreateAdvisorConversationRequest,
        current_user: AuthUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        """Create an advisor conversation, optionally anchored to saved research."""
        title = payload.title.strip()
        active_topic = payload.active_topic.strip()
        active_entities = list(payload.active_entities)
        active_themes = list(payload.active_themes)
        if payload.active_research_run_id:
            run = db.get_run(payload.active_research_run_id, user_id=current_user.id)
            if not run:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found")
            title = title or f"{run['ticker']} research discussion"
            active_topic = active_topic or f"{run['ticker']} research"
            active_entities = _dedupe_strings([*active_entities, run["ticker"], run["company_name"]])
        conversation = db.create_advisor_conversation(
            user_id=current_user.id,
            title=title or "Advisor conversation",
            active_topic=active_topic,
            active_research_run_id=payload.active_research_run_id,
            active_generic_research_id=payload.active_generic_research_id,
            active_entities=active_entities,
            active_themes=active_themes,
        )
        return _public_advisor_conversation(conversation, include_messages=True)

    @router.get("/conversations/{conversation_id}", response_model=AdvisorConversationResponse)
    async def get_conversation(
        conversation_id: str,
        current_user: AuthUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        """Load one advisor conversation with all messages."""
        row = db.get_advisor_conversation(conversation_id, current_user.id)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Advisor conversation not found")
        return _public_advisor_conversation(row, include_messages=True)

    @router.post("/runs", response_model=AdvisorRunResponse)
    async def create_advisor_run(
        payload: AdvisorRunRequest,
        current_user: AuthUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        """Run one advisor turn for the signed-in user.

        Both the agentic harness and the legacy pipeline are fully synchronous
        (DB + LLM calls), so we offload to a thread-pool executor to keep the
        uvicorn event loop free.
        """
        loop = asyncio.get_event_loop()
        if _ADVISOR_AGENT_ENABLED:
            fn = lambda: run_advisor_agent_turn(user_id=current_user.id, payload=payload)
        else:
            fn = lambda: run_advisor_harness(user_id=current_user.id, payload=payload)
        return await loop.run_in_executor(None, fn)

    @router.get("/runs")
    async def list_runs(
        limit: int = Query(default=20, ge=1, le=100),
        current_user: AuthUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        """List recent advisor harness runs."""
        items = [_public_advisor_run(row) for row in db.list_advisor_runs(current_user.id, limit=limit)]
        return {"items": items, "total": len(items)}

    @router.get("/runs/{run_id}", response_model=AdvisorRunResponse)
    async def get_run(run_id: str, current_user: AuthUser = Depends(require_current_user)) -> dict[str, Any]:
        """Get one advisor harness run."""
        row = db.get_advisor_run(run_id, current_user.id)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Advisor run not found")
        return _public_advisor_run(row)

    return router
