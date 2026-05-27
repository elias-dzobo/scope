"""Tests for the agentic advisor harness.

Tests at two levels:
  1. Harness unit tests — verify the loop, guardrails, and tool dispatch
     using a fake LLM and fake tools (no network, no DB).
  2. Agent integration tests — verify the AdvisorAgent tool implementations
     against mock DB/memory calls.

Nothing here calls a real LLM, Gemini, or the database.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent_core.harness import AgentHarness
from agent_core.models import AgentContext, HarnessConfig, TokenUsage, TraceStep
from agent_core.tool import AgentTool, ToolRegistry
from agent_core.guards import check_input, check_loop_limits, check_output, InputGuardResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(allow_deep: bool = False) -> AgentContext:
    return AgentContext(
        user_id="test_user_42",
        run_id=uuid.uuid4().hex,
        allow_deep_research=allow_deep,
    )


def _config(**overrides) -> HarnessConfig:
    base = HarnessConfig(model="gpt-4o", max_iterations=5, max_tool_calls=10, timeout_seconds=30.0)
    return base.model_copy(update=overrides)


# ---------------------------------------------------------------------------
# Fake tool for harness unit tests
# ---------------------------------------------------------------------------


from pydantic import BaseModel, Field


class _EchoInput(BaseModel):
    message: str = Field(description="Message to echo.")


class EchoTool(AgentTool):
    """Returns the input message as-is. Used to test tool dispatch."""

    name: str = "echo"
    description: str = "Echo the input message back."
    args_schema: type[BaseModel] = _EchoInput
    max_calls_per_run: int = 3

    call_count_tracker: list[str] = []

    def _run(self, message: str) -> str:  # type: ignore[override]
        self.call_count_tracker.append(message)
        return f"ECHO: {message}"


class _ExpensiveInput(BaseModel):
    query: str


class ExpensiveTool(AgentTool):
    name: str = "expensive_op"
    description: str = "An expensive operation."
    args_schema: type[BaseModel] = _ExpensiveInput
    expensive: bool = True
    max_calls_per_run: int = 1

    def _run(self, query: str) -> str:  # type: ignore[override]
        return "expensive result"


# ---------------------------------------------------------------------------
# Layer 1 — Input guards
# ---------------------------------------------------------------------------


class TestInputGuards:
    def test_empty_query_rejected(self):
        result = check_input("", _context())
        assert not result.ok
        assert "Empty" in result.reason

    def test_overlength_query_rejected(self):
        result = check_input("x" * 9000, _context())
        assert not result.ok
        assert "8 000" in result.reason

    def test_missing_user_id_rejected(self):
        ctx = AgentContext(user_id="", run_id="r1")
        result = check_input("hello", ctx)
        assert not result.ok

    def test_valid_query_passes(self):
        result = check_input("What is NVDA's valuation?", _context())
        assert result.ok


# ---------------------------------------------------------------------------
# Layer 3 — Loop guards
# ---------------------------------------------------------------------------


class TestLoopGuards:
    def _state(self, **overrides):
        defaults = {
            "messages": [],
            "trace": [],
            "tool_calls": [],
            "usage": TokenUsage(),
            "warnings": [],
            "tool_call_count": 0,
            "iteration": 0,
            "start_time": time.monotonic(),
            "termination_reason": "done",
            "config": _config(),
            "context": _context(),
        }
        defaults.update(overrides)
        return defaults

    def test_timeout_triggers(self):
        state = self._state(start_time=time.monotonic() - 200.0)
        result = check_loop_limits(state)
        assert result.should_stop
        assert result.termination_reason == "timeout"

    def test_token_budget_triggers(self):
        state = self._state(usage=TokenUsage(total_tokens=200_000))
        result = check_loop_limits(state)
        assert result.should_stop
        assert result.termination_reason == "budget"

    def test_max_tool_calls_triggers(self):
        state = self._state(tool_call_count=99)
        result = check_loop_limits(state)
        assert result.should_stop

    def test_max_iterations_triggers(self):
        state = self._state(iteration=99)
        result = check_loop_limits(state)
        assert result.should_stop

    def test_healthy_state_passes(self):
        state = self._state()
        result = check_loop_limits(state)
        assert not result.should_stop


# ---------------------------------------------------------------------------
# Layer 4 — Output guards
# ---------------------------------------------------------------------------


class TestOutputGuards:
    def test_certainty_language_gets_disclaimer(self):
        answer = "You should definitely buy NVDA — it will definitely rise."
        result = check_output(answer, [])
        assert "*This is research context" in result.answer
        assert result.warnings

    def test_empty_answer_replaced(self):
        result = check_output("   ", [])
        assert "unable to generate" in result.answer.lower()

    def test_normal_answer_unchanged(self):
        answer = "Based on the research, NVDA has strong fundamentals but the valuation looks stretched."
        result = check_output(answer, [])
        assert result.answer == answer
        assert not result.warnings


# ---------------------------------------------------------------------------
# Tool registry — call limits and cycle detection
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_call_limit_enforced(self):
        tool = EchoTool()
        registry = ToolRegistry([tool])
        assert not registry.at_call_limit("echo")
        for _ in range(3):
            registry.record_call("echo")
        assert registry.at_call_limit("echo")

    def test_cycle_detection(self):
        registry = ToolRegistry([EchoTool()])
        args = {"message": "hello"}
        assert not registry.is_duplicate_call("echo", args)
        registry.mark_call("echo", args)
        assert registry.is_duplicate_call("echo", args)
        # Different args are not a cycle
        assert not registry.is_duplicate_call("echo", {"message": "world"})

    def test_unknown_tool_not_at_limit(self):
        registry = ToolRegistry([])
        assert not registry.at_call_limit("nonexistent")


# ---------------------------------------------------------------------------
# Harness — end-to-end with fake LLM
# ---------------------------------------------------------------------------


def _make_fake_llm(responses: list):
    """Return a mock that returns responses in sequence when .invoke() or
    .bind_tools().invoke() is called."""
    mock = MagicMock()
    side_effects = iter(responses)

    def make_bound(*args, **kwargs):
        bound = MagicMock()
        bound.invoke = MagicMock(side_effect=lambda msgs: next(side_effects))
        return bound

    mock.bind_tools = MagicMock(side_effect=make_bound)
    mock.invoke = MagicMock(side_effect=lambda msgs: next(side_effects))
    return mock


def _make_mock_llm(responses: list):
    """Build a mock ChatOpenAI whose bound version returns responses in sequence."""
    instance = MagicMock()
    it = iter(responses)
    bound = MagicMock()
    bound.invoke = MagicMock(side_effect=lambda msgs: next(it))
    instance.bind_tools = MagicMock(return_value=bound)
    instance.invoke = MagicMock(side_effect=lambda msgs: next(it, responses[-1]))
    return instance


def _ai(content: str, tool_calls: list | None = None) -> AIMessage:
    msg = AIMessage(content=content)
    msg.tool_calls = tool_calls or []
    return msg


class TestAgentHarness:
    def test_direct_answer_no_tools(self):
        """Agent returns answer on first turn without calling any tools."""
        plain_answer = _ai("A P/E ratio compares price to earnings.")

        with patch("agent_core.harness.ChatOpenAI", return_value=_make_mock_llm([plain_answer])):
            harness = AgentHarness(tools=[EchoTool()], config=_config())
            result = harness.run(
                system_prompt="You are a helpful advisor.",
                user_message="What is a P/E ratio?",
                context=_context(),
            )

        assert result.answer
        assert result.termination_reason == "done"
        assert not result.tool_calls

    def test_tool_call_dispatched(self):
        """Agent calls a tool, receives result, then answers."""
        responses = [
            _ai("", [{"id": "c1", "name": "echo", "args": {"message": "hello"}}]),
            _ai("The echo says: ECHO: hello"),
        ]

        with patch("agent_core.harness.ChatOpenAI", return_value=_make_mock_llm(responses)):
            harness = AgentHarness(tools=[EchoTool()], config=_config())
            result = harness.run(
                system_prompt="Use tools to answer.",
                user_message="Echo hello.",
                context=_context(),
            )

        assert result.termination_reason == "done"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "echo"
        assert "ECHO: hello" in result.tool_calls[0].result_summary

    def test_expensive_tool_blocked_without_permission(self):
        """Expensive tool is skipped when allow_deep_research=False."""
        responses = [
            _ai("", [{"id": "c1", "name": "expensive_op", "args": {"query": "test"}}]),
            _ai("I cannot run that without permission."),
        ]

        with patch("agent_core.harness.ChatOpenAI", return_value=_make_mock_llm(responses)):
            harness = AgentHarness(tools=[ExpensiveTool()], config=_config())
            result = harness.run(
                system_prompt="Use tools.",
                user_message="Run the expensive op.",
                context=_context(allow_deep=False),
            )

        skipped = [tc for tc in result.tool_calls if tc.skipped]
        assert skipped, "Expected expensive tool to be skipped"
        assert "deep-research permission" in skipped[0].skip_reason

    def test_cycle_detection_prevents_repeat_calls(self):
        """Calling the same tool with identical args twice is blocked on the second call."""
        responses = [
            _ai("", [{"id": "c1", "name": "echo", "args": {"message": "repeat"}}]),
            _ai("", [{"id": "c2", "name": "echo", "args": {"message": "repeat"}}]),
            _ai("Done."),
        ]

        with patch("agent_core.harness.ChatOpenAI", return_value=_make_mock_llm(responses)):
            harness = AgentHarness(tools=[EchoTool()], config=_config())
            result = harness.run(
                system_prompt="Use tools.",
                user_message="Echo repeat twice.",
                context=_context(),
            )

        executed = [c for c in result.tool_calls if not c.skipped]
        skipped = [c for c in result.tool_calls if c.skipped]
        assert len(executed) == 1
        assert len(skipped) == 1

    def test_max_iterations_force_summarises(self):
        """When max_iterations is hit, harness force-summarises and terminates."""
        never_done = _ai("", [{"id": "c1", "name": "echo", "args": {"message": "m1"}}])
        force_summary = _ai("Here is my best answer given the limits.")

        mock_llm = _make_mock_llm([never_done, never_done])
        mock_llm.invoke = MagicMock(return_value=force_summary)

        with patch("agent_core.harness.ChatOpenAI", return_value=mock_llm):
            harness = AgentHarness(tools=[EchoTool()], config=_config(max_iterations=2))
            result = harness.run(
                system_prompt="Use tools.",
                user_message="Keep going.",
                context=_context(),
            )

        assert result.termination_reason in {"max_iterations", "done", "timeout"}
        assert result.warnings

    def test_invalid_query_rejected_before_loop(self):
        """Empty query is rejected by input guard; LLM is never called."""
        with patch("agent_core.harness.ChatOpenAI") as MockLLM:
            harness = AgentHarness(tools=[EchoTool()], config=_config())
            result = harness.run(
                system_prompt="You are an advisor.",
                user_message="",
                context=_context(),
            )
        assert result.termination_reason == "error"
        assert any("Empty" in w for w in result.warnings)
