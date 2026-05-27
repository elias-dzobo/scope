"""Core data models for the agent harness.

These are the shared types that flow through every agent run — state,
configuration, results, and trace records. Keeping them in one place means
every layer of the system (harness, tools, guards, agents) uses the same
vocabulary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


# ---------------------------------------------------------------------------
# Trace — every decision and action is recorded
# ---------------------------------------------------------------------------


class TraceStep(BaseModel):
    """One observable event in the agent's reasoning or execution."""

    type: Literal["reasoning", "tool_call", "tool_result", "guardrail", "warning", "system"]
    name: str
    status: Literal["started", "completed", "skipped", "failed"]
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: int | None = None


class ToolCallRecord(BaseModel):
    """Structured record of one tool invocation — used for cycle detection and audit."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""
    error: str | None = None
    skipped: bool = False
    skip_reason: str = ""
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Harness configuration
# ---------------------------------------------------------------------------


class HarnessConfig(BaseModel):
    """All tuneable knobs for one agent run.

    Defaults are conservative. Expensive agents (research) should be given
    longer timeouts; quick advisor turns should be tighter.
    """

    model: str = "gpt-4o"
    temperature: float = 0.1
    max_iterations: int = 10           # reasoning rounds before force-summarise
    max_tool_calls: int = 20           # total tool invocations across all rounds
    timeout_seconds: float = 120.0    # wall-clock budget for the whole run
    token_budget: int = 100_000        # cumulative prompt + completion tokens
    allow_parallel_tools: bool = True  # execute multiple tool calls concurrently


# ---------------------------------------------------------------------------
# Per-run context — injected into every tool call
# ---------------------------------------------------------------------------


class AgentContext(BaseModel):
    """Run-scoped context available to all tools.

    Tools must not hold references to this beyond a single call — it is
    re-injected fresh by the harness on every invocation.
    """

    model_config = {"arbitrary_types_allowed": True}

    user_id: str
    run_id: str
    allow_deep_research: bool = False   # gate: user must opt in to expensive ops
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Shared mutable state within a run.
    # Tools can write structured data here (e.g. accumulated evidence list).
    # The harness copies it into AgentResult.metadata at the end.
    state: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# LangGraph state — the single source of truth across the agent loop
# ---------------------------------------------------------------------------


class HarnessState(TypedDict):
    """Carries everything across graph nodes.

    LangGraph merges partial dicts returned by each node with the current
    state.  Fields not returned by a node stay unchanged.
    """

    # Message history — LangGraph accumulates these automatically
    messages: Annotated[list[BaseMessage], add_messages]

    # Observability
    trace: list[TraceStep]
    tool_calls: list[ToolCallRecord]
    usage: TokenUsage
    warnings: list[str]

    # Loop bookkeeping — harness reads these to enforce limits
    tool_call_count: int
    iteration: int
    start_time: float
    termination_reason: str

    # Injected once at graph construction, treated as read-only
    config: HarnessConfig
    context: AgentContext


# ---------------------------------------------------------------------------
# Final result returned to callers
# ---------------------------------------------------------------------------


class AgentResult(BaseModel):
    answer: str
    trace: list[TraceStep] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    termination_reason: Literal["done", "max_iterations", "timeout", "budget", "error"] = "done"
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
