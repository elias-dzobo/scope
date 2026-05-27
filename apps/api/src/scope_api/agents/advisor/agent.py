"""AdvisorAgent — ReAct loop with deterministic context injection.

Architecture overview (from Anthropic's agentic systems guide):

    ┌──────────────────────────────────────────────────────┐
    │  1. Context Loader (Python, deterministic, pre-LLM)  │
    │     Loads: research results, recent messages,        │
    │     conversation state, profile hint → injected into │
    │     system prompt BEFORE the LLM sees anything.      │
    ├──────────────────────────────────────────────────────┤
    │  2. ReAct Loop (LLM-as-orchestrator)                 │
    │     max_iterations=5, max_tool_calls=8               │
    │     Tools: fast only (DB reads + quick web search)   │
    │     Terminates when LLM decides it has enough info.  │
    ├──────────────────────────────────────────────────────┤
    │  3. Structured Synthesis (forced output schema)      │
    │     Maps free-text agent answer → AdvisorAnswerShape │
    │     with stance, confidence, keyPoints, evidenceRefs │
    └──────────────────────────────────────────────────────┘

Why this is better than the old design:
  Old: agent starts cold, discovers context through tool calls, no structured output.
  New: agent starts informed (context loader), terminates faster, produces typed output.

Usage::

    agent = AdvisorAgent()
    result = agent.run(
        user_id="u123",
        query="Is NVDA still a good hold given my risk profile?",
        conversation_id="c456",
        allow_deep_research=True,
    )
    print(result.answer)
    print(result.metadata["structuredAnswer"])  # typed fields
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from agent_core.harness import AgentHarness
from agent_core.models import AgentContext, AgentResult, HarnessConfig
from research_core.utils.logger import get_logger

from .context import build_injected_context
from .prompts import ADVISOR_SYSTEM_PROMPT
from .tools import build_advisor_tools

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Harness config
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_CONFIG = HarnessConfig(
    model="gpt-4o",
    temperature=0.1,
    # 5 iterations is enough: context loader means most questions need 0-2 tool calls.
    # The old value of 8 encouraged over-tooling.
    max_iterations=5,
    max_tool_calls=8,
    # 75s leaves 15s headroom before the FastAPI route's 90s outer timeout.
    timeout_seconds=75.0,
    token_budget=80_000,
    allow_parallel_tools=True,
)


# ──────────────────────────────────────────────────────────────────────────────
# Structured answer shape (produced post-ReAct via a synthesis step)
# ──────────────────────────────────────────────────────────────────────────────

_SYNTHESIS_SYSTEM = """You are a structured-output extractor for an investment advisor.

Given the advisor's free-text answer, extract the following fields.
Be accurate — do not invent information that was not in the answer.

Return JSON with these fields:
{
  "stance": one of "bullish" | "cautious" | "bearish" | "neutral" | "needs_more_data" | "answered_from_memory" | "background_research_queued",
  "confidence": one of "high" | "medium" | "low",
  "keyPoints": list of 2-4 concise bullet strings (the most important things said),
  "limitations": list of 0-3 limitations or caveats mentioned,
  "nextActions": list of 0-2 concrete next steps mentioned
}

Rules:
- stance reflects the tone of the answer about the investment, not a meta-comment.
  Use "needs_more_data" if the advisor said there isn't enough info.
  Use "background_research_queued" if the advisor said research was just queued.
  Use "answered_from_memory" if the answer came entirely from saved research.
- confidence: "high" only if the answer cites specific data with clear conclusions.
- keyPoints: actual insights from the answer, not just re-statements of the question.
- Keep each keyPoint under 120 characters.
"""


def _extract_structured_answer(answer_text: str) -> dict[str, Any]:
    """Run a fast structured-extraction pass on the agent's free-text answer.

    Returns a dict with stance, confidence, keyPoints, limitations, nextActions.
    Falls back gracefully if the model call fails.
    """
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return _default_structured_answer()

    try:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        response = model.invoke(
            [
                ("system", _SYNTHESIS_SYSTEM),
                ("human", f"Advisor answer:\n\n{answer_text[:3000]}"),
            ]
        )
        content = str(getattr(response, "content", response)).strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.splitlines()
            content = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()
        parsed = json.loads(content)
        return {
            "stance": str(parsed.get("stance") or "neutral"),
            "confidence": str(parsed.get("confidence") or "medium"),
            "keyPoints": list(parsed.get("keyPoints") or [])[:4],
            "limitations": list(parsed.get("limitations") or [])[:3],
            "nextActions": list(parsed.get("nextActions") or [])[:2],
        }
    except Exception as exc:
        logger.debug("Structured answer extraction failed: %s", exc)
        return _default_structured_answer()


def _default_structured_answer() -> dict[str, Any]:
    return {
        "stance": "neutral",
        "confidence": "medium",
        "keyPoints": [],
        "limitations": [],
        "nextActions": [],
    }


# ──────────────────────────────────────────────────────────────────────────────
# AdvisorAgent
# ──────────────────────────────────────────────────────────────────────────────


class AdvisorAgent:
    """One agent instance = one compiled harness.

    Compiled once at construction, reused across requests.
    Context is injected fresh each turn via build_injected_context().
    """

    def __init__(self, config: HarnessConfig | None = None) -> None:
        self.config = config or _DEFAULT_CONFIG
        self._harness = AgentHarness(
            tools=build_advisor_tools(),
            config=self.config,
        )
        logger.info("AdvisorAgent compiled | model=%s", self.config.model)

    def run(
        self,
        *,
        user_id: str,
        query: str,
        conversation_id: str = "",
        conversation: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
        allow_deep_research: bool = False,
        run_id: str | None = None,
    ) -> AgentResult:
        """Execute one advisor turn.

        Parameters
        ----------
        user_id:
            Authenticated user ID.
        query:
            Raw user query (already augmented with conversation context by the
            caller if needed).
        conversation_id:
            Used for the context loader to fetch linked research.
        conversation:
            The conversation DB row — passed through to avoid a redundant DB
            fetch if the caller already has it. If None, context loader fetches it.
        messages:
            Recent messages for the context injection. If None, context loader
            fetches them.
        allow_deep_research:
            When True, the trigger_background_research tool is unlocked.
            The agent respects the tool's own guard (checks for recent runs first).
        run_id:
            Auto-generated if not provided.
        """
        run_id = run_id or uuid.uuid4().hex

        # ── 1. Context loader — runs before the LLM sees anything ───────────
        # This is the key architectural improvement: instead of making the agent
        # discover its context through tool calls, we load it deterministically
        # in Python and inject it into the system prompt.
        injected_context = ""
        if conversation is not None and messages is not None:
            # Caller already fetched them — use directly (avoids DB round-trip)
            injected_context = build_injected_context(
                user_id=user_id,
                conversation=conversation,
                messages=messages,
            )
        elif conversation_id:
            # Load from DB
            try:
                from scope_api import db

                conv = db.get_advisor_conversation(conversation_id, user_id)
                msgs = db.list_advisor_messages(conversation_id, user_id) if conv else []
                injected_context = build_injected_context(
                    user_id=user_id,
                    conversation=conv or {},
                    messages=msgs,
                )
            except Exception as exc:
                logger.warning("Context loader DB fetch failed: %s", exc)

        system_prompt = ADVISOR_SYSTEM_PROMPT
        if injected_context:
            system_prompt = injected_context + "\n\n" + system_prompt

        # ── 2. AgentContext ──────────────────────────────────────────────────
        context = AgentContext(
            user_id=user_id,
            run_id=run_id,
            allow_deep_research=allow_deep_research,
            metadata={
                "conversation_id": conversation_id,
                "original_query": query,
            },
        )

        logger.info(
            "AdvisorAgent run | run_id=%s user_id=%s allow_deep=%s injected_ctx=%d chars",
            run_id,
            user_id,
            allow_deep_research,
            len(injected_context),
        )

        # ── 3. ReAct loop ────────────────────────────────────────────────────
        result = self._harness.run(
            system_prompt=system_prompt,
            user_message=query,
            context=context,
        )

        # ── 4. Structured synthesis ──────────────────────────────────────────
        # The ReAct loop produces a free-text answer. We run one fast extraction
        # pass to pull out typed fields (stance, confidence, keyPoints, etc.)
        # so the frontend can display structured metadata without the agent
        # needing to produce JSON directly in its reasoning loop.
        if result.answer:
            structured = _extract_structured_answer(result.answer)
            result.metadata["structuredAnswer"] = structured
            logger.debug(
                "Structured answer | run_id=%s stance=%s confidence=%s",
                run_id,
                structured.get("stance"),
                structured.get("confidence"),
            )

        return result
