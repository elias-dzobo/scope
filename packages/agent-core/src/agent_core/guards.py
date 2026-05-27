"""Guardrail implementations for the agent harness.

Guardrails are not advisory — they are enforced by the harness regardless
of what the agent decides.  Four layers:

  Layer 1  Input     — validate the query before the loop starts
  Layer 2  Tool      — check per-tool preconditions at dispatch time
  Layer 3  Loop      — enforce iteration, time, and token limits mid-run
  Layer 4  Output    — sanitise and calibrate the final answer

Each layer is a plain function or a small class.  They are called by the
harness nodes; agent code never calls them directly.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .models import AgentContext, HarnessConfig, HarnessState


# ---------------------------------------------------------------------------
# Layer 1 — Input guards
# ---------------------------------------------------------------------------


@dataclass
class InputGuardResult:
    ok: bool
    reason: str = ""


def check_input(query: str, context: AgentContext) -> InputGuardResult:
    """Validate the query before the loop starts."""
    query = query.strip()

    if not query:
        return InputGuardResult(ok=False, reason="Empty query.")

    if len(query) > 8_000:
        return InputGuardResult(ok=False, reason="Query exceeds 8 000 character limit.")

    # user_id must be present — caught here so tools never see a blank user
    if not context.user_id:
        return InputGuardResult(ok=False, reason="No user_id in context.")

    return InputGuardResult(ok=True)


# ---------------------------------------------------------------------------
# Layer 2 — Tool-level guards  (called at dispatch time by the harness)
# ---------------------------------------------------------------------------


def check_tool_allowed(
    tool_name: str,
    args: dict[str, Any],
    registry: Any,           # ToolRegistry — avoid circular import
    context: AgentContext,
) -> str | None:
    """Return a veto reason string or None to allow execution.

    Checks (in order):
      1. Tool exists in registry
      2. Tool has not exceeded its per-run call limit
      3. Tool is not a duplicate call (cycle detection)
      4. Expensive tool requires explicit permission
      5. Tool's own precondition check
    """
    tool = registry.get(tool_name)
    if tool is None:
        return f"Unknown tool '{tool_name}'."

    if registry.at_call_limit(tool_name):
        limit = tool.max_calls_per_run
        return f"Tool '{tool_name}' has reached its per-run call limit ({limit})."

    if registry.is_duplicate_call(tool_name, args):
        return (
            f"Tool '{tool_name}' was already called with identical arguments. "
            "Use the earlier result or refine your approach."
        )

    if tool.expensive and not context.allow_deep_research:
        return (
            f"Tool '{tool_name}' requires deep-research permission which was not "
            "granted for this session. Inform the user and ask them to enable it."
        )

    precondition_err = tool.check_preconditions(**args)
    if precondition_err:
        return precondition_err

    return None


# ---------------------------------------------------------------------------
# Layer 3 — Loop guards  (checked at the start of each reasoning iteration)
# ---------------------------------------------------------------------------


@dataclass
class LoopGuardResult:
    should_stop: bool
    reason: str = ""
    termination_reason: str = "done"


def check_loop_limits(state: HarnessState) -> LoopGuardResult:
    """Inspect the current state and decide whether the loop must terminate."""
    cfg: HarnessConfig = state["config"]
    elapsed = time.monotonic() - state["start_time"]

    if elapsed > cfg.timeout_seconds:
        return LoopGuardResult(
            should_stop=True,
            reason=f"Wall-clock timeout ({cfg.timeout_seconds}s) exceeded after {elapsed:.0f}s.",
            termination_reason="timeout",
        )

    usage = state["usage"]
    if usage.total_tokens > cfg.token_budget:
        return LoopGuardResult(
            should_stop=True,
            reason=f"Token budget ({cfg.token_budget:,}) exceeded ({usage.total_tokens:,} used).",
            termination_reason="budget",
        )

    if state["tool_call_count"] >= cfg.max_tool_calls:
        return LoopGuardResult(
            should_stop=True,
            reason=f"Max tool calls ({cfg.max_tool_calls}) reached.",
            termination_reason="max_iterations",
        )

    if state["iteration"] >= cfg.max_iterations:
        return LoopGuardResult(
            should_stop=True,
            reason=f"Max iterations ({cfg.max_iterations}) reached.",
            termination_reason="max_iterations",
        )

    return LoopGuardResult(should_stop=False)


# ---------------------------------------------------------------------------
# Layer 4 — Output guards  (run on the final answer before returning)
# ---------------------------------------------------------------------------

# Patterns that suggest overly certain financial advice
_CERTAINTY_PATTERNS = re.compile(
    r"\b(guaranteed|will definitely|certain(ly)?|without (a )?doubt|100%\s*sure|"
    r"you must buy|you must sell|definitely buy|definitely sell)\b",
    re.IGNORECASE,
)

_DISCLAIMER = (
    "\n\n*This is research context, not financial advice. "
    "Past performance does not guarantee future results.*"
)


@dataclass
class OutputGuardResult:
    answer: str
    warnings: list[str] = field(default_factory=list)


def check_output(answer: str, warnings: list[str]) -> OutputGuardResult:
    """Sanitise the final answer and attach calibration warnings."""
    out_warnings = list(warnings)

    # Soften absolute certainty language
    if _CERTAINTY_PATTERNS.search(answer):
        out_warnings.append(
            "Output contained high-certainty financial language — disclaimer appended."
        )
        answer = answer + _DISCLAIMER

    # Ensure answer is not empty
    if not answer.strip():
        answer = "I was unable to generate a response. Please try rephrasing your question."
        out_warnings.append("Empty answer replaced with fallback message.")

    return OutputGuardResult(answer=answer, warnings=out_warnings)
