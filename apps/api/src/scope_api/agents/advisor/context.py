"""Context loader for the Advisor Agent.

This runs BEFORE the LLM touches anything. It deterministically loads all
context the agent is likely to need and injects it directly into the system
prompt. This means the agent starts each turn already informed — it does not
have to discover context through tool calls.

Design principle (from Anthropic's agent guide):
    "Context engineering over prompt engineering. If you know what context
     the agent always needs, load it deterministically — don't make the
     agent discover it through tool calls."

What gets injected:
  1. Conversation topic + active entities  (from conversation row)
  2. Linked research run summary           (from research_runs + result)
  3. Recent conversation messages          (last 4 turns)
  4. User profile hint                     (lightweight — full profile via tool)

The agent should read this block first. If the research it needs is already
here it must NOT call get_research_results — that is enforced in the prompt.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from research_core.utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def build_injected_context(
    *,
    user_id: str,
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
) -> str:
    """Return a formatted context block ready to prepend to the system prompt.

    Never raises — failures produce an empty section so the agent can still run.
    """
    sections: list[str] = []

    _add_conversation_section(sections, conversation)
    _add_linked_research_section(sections, user_id, conversation)
    _add_recent_messages_section(sections, messages)
    _add_profile_hint_section(sections, user_id)

    if not sections:
        return ""

    header = "━" * 60
    return (
        f"\n{header}\n"
        "INJECTED CONTEXT  (loaded before your first reasoning step)\n"
        f"{header}\n"
        + "\n\n".join(sections)
        + f"\n{header}\n"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Sections
# ──────────────────────────────────────────────────────────────────────────────


def _add_conversation_section(sections: list[str], conversation: dict[str, Any]) -> None:
    topic = str(conversation.get("active_topic") or "").strip()
    entities = [str(e) for e in (conversation.get("active_entities") or []) if str(e).strip()]
    themes = [str(t) for t in (conversation.get("active_themes") or []) if str(t).strip()]

    if not (topic or entities or themes):
        return

    lines = ["[CONVERSATION STATE]"]
    if topic:
        lines.append(f"Topic: {topic}")
    if entities:
        lines.append(f"Active entities: {', '.join(entities[:8])}")
    if themes:
        lines.append(f"Active themes: {', '.join(themes[:6])}")
    sections.append("\n".join(lines))


def _add_linked_research_section(
    sections: list[str],
    user_id: str,
    conversation: dict[str, Any],
) -> None:
    """If the conversation is linked to a completed research run, inject its summary."""
    from scope_api import db

    run_id = str(conversation.get("active_research_run_id") or "").strip()
    if not run_id:
        return

    try:
        run = db.get_run(run_id, user_id=user_id)
        if not run or run.get("status") != "completed":
            return

        result = run.get("result") or {}
        if not result:
            return

        ticker = str(run.get("ticker") or "").upper()
        company = str(run.get("company_name") or ticker)
        created = str(run.get("created_at") or "")[:10]
        age_days = _age_days(run.get("created_at"))

        synthesis = result.get("final_synthesis") or {}
        scorecard = result.get("scorecard") or {}

        lines = [f"[LINKED RESEARCH: {company} ({ticker}) — completed {created}, {age_days}d ago]"]

        score = scorecard.get("overall_score")
        rec = scorecard.get("recommendation")
        if score or rec:
            lines.append(f"Score: {score}  |  Recommendation: {rec}")

        takeaway = str(synthesis.get("investmentTakeaway") or "")[:500]
        if takeaway:
            lines.append(f"Investment takeaway: {takeaway}")

        snapshot = str(synthesis.get("companySnapshot") or "")[:300]
        if snapshot:
            lines.append(f"Company: {snapshot}")

        risks = list(synthesis.get("mainRisks") or [])[:3]
        if risks:
            lines.append(f"Main risks: {'; '.join(str(r) for r in risks)}")

        catalysts = list(synthesis.get("catalysts") or [])[:2]
        if catalysts:
            lines.append(f"Catalysts: {'; '.join(str(c) for c in catalysts)}")

        # Pillar scores if present
        pillars = result.get("pillars") or {}
        if isinstance(pillars, dict) and pillars:
            pillar_lines = [
                f"  {name}: {str(data.get('score') or data.get('verdict') or '')}"
                for name, data in list(pillars.items())[:4]
                if isinstance(data, dict)
            ]
            if pillar_lines:
                lines.append("Pillar scores:\n" + "\n".join(pillar_lines))

        sections.append("\n".join(lines))
        logger.debug("Injected linked research run=%s ticker=%s", run_id, ticker)

    except Exception as exc:
        logger.warning("Failed to inject linked research run=%s: %s", run_id, exc)


def _add_recent_messages_section(
    sections: list[str],
    messages: list[dict[str, Any]],
) -> None:
    if not messages:
        return

    recent = messages[-4:]
    lines = ["[RECENT CONVERSATION]"]
    for m in recent:
        role = str(m.get("role") or "").upper()
        content = str(m.get("content") or "")[:400].strip()
        if content:
            lines.append(f"{role}: {content}")

    if len(lines) > 1:
        sections.append("\n".join(lines))


def _add_profile_hint_section(sections: list[str], user_id: str) -> None:
    """Inject a one-line profile hint so the agent knows the user's frame."""
    from scope_api import db

    try:
        profile = db.get_onboarding_profile(user_id)
        if not profile:
            return

        answers = profile.get("answers") or {}
        risk = str(answers.get("riskTolerance") or answers.get("risk_tolerance") or "").strip()
        horizon = str(answers.get("investmentHorizon") or answers.get("investment_horizon") or "").strip()
        goals = str(answers.get("primaryGoal") or answers.get("primary_goal") or "").strip()

        hint_parts = []
        if risk:
            hint_parts.append(f"risk={risk}")
        if horizon:
            hint_parts.append(f"horizon={horizon}")
        if goals:
            hint_parts.append(f"goal={goals}")

        if hint_parts:
            sections.append(f"[USER PROFILE HINT] {', '.join(hint_parts)}\n(call get_user_profile for full details)")

    except Exception as exc:
        logger.debug("Profile hint load failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _age_days(created_at: Any) -> int:
    if not created_at:
        return 0
    try:
        dt = datetime.fromisoformat(str(created_at)).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 0
