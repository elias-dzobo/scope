"""Tool interface for the agent harness.

Every tool the advisor or research agent can call is a subclass of
``AgentTool``.  It extends LangChain's ``BaseTool`` so LangGraph's
``ToolNode`` can execute it natively, but adds harness-specific metadata:

- ``expensive``          — signals that cost-gate checks must pass before
                           the harness allows execution
- ``max_calls_per_run``  — hard cap enforced by the harness, not the LLM
- ``check_preconditions``— tool-level guard that can veto execution with a
                           human-readable reason

Context injection
-----------------
The harness calls ``set_context(ctx)`` on every tool before the graph runs.
Tools read ``self.ctx`` inside ``_run`` to access the current user, run ID,
and any shared run-level state (e.g. the research evidence accumulator).
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from langchain_core.tools import BaseTool

from .models import AgentContext


class AgentTool(BaseTool):
    """Base for all harness tools.

    Subclasses must implement ``_run`` and set ``name``, ``description``,
    and ``args_schema`` (a Pydantic model describing the tool's parameters).
    """

    # Harness metadata — declare as ClassVar-like fields at class level
    expensive: bool = False          # requires allow_deep_research=True in context
    max_calls_per_run: int = 10

    # Injected by harness before the graph starts — do not set manually
    ctx: AgentContext | None = None

    model_config = {"arbitrary_types_allowed": True}

    def set_context(self, context: AgentContext) -> None:
        """Called by the harness once per run before any tool executions."""
        self.ctx = context

    def check_preconditions(self, **kwargs: Any) -> str | None:
        """Return a non-empty string (the veto reason) to skip execution.

        The harness calls this before every invocation.  If it returns a
        string the tool is skipped and the string is fed back to the agent
        so it can reason about the constraint.  Return ``None`` to proceed.
        """
        return None

    @abstractmethod
    def _run(self, **kwargs: Any) -> str:  # type: ignore[override]
        """Execute the tool synchronously.  Return a string the LLM can read."""
        ...

    async def _arun(self, **kwargs: Any) -> str:  # type: ignore[override]
        raise NotImplementedError("Async tool execution not supported — use thread executor.")


class ToolRegistry:
    """Holds all tools registered for one agent run and tracks call counts."""

    def __init__(self, tools: list[AgentTool]) -> None:
        self._tools: dict[str, AgentTool] = {t.name: t for t in tools}
        self._counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def all(self) -> list[AgentTool]:
        return list(self._tools.values())

    # ------------------------------------------------------------------
    # Call-limit tracking
    # ------------------------------------------------------------------

    def record_call(self, name: str) -> None:
        self._counts[name] = self._counts.get(name, 0) + 1

    def call_count(self, name: str) -> int:
        return self._counts.get(name, 0)

    def at_call_limit(self, name: str) -> bool:
        tool = self.get(name)
        if tool is None:
            return False
        return self.call_count(name) >= tool.max_calls_per_run

    # ------------------------------------------------------------------
    # Cycle detection — same tool + same args seen before?
    # ------------------------------------------------------------------

    def _fingerprint(self, name: str, args: dict[str, Any]) -> str:
        import json
        return f"{name}::{json.dumps(args, sort_keys=True, default=str)}"

    def __init_cycle_set(self) -> None:
        if not hasattr(self, "_seen_calls"):
            self._seen_calls: set[str] = set()

    def is_duplicate_call(self, name: str, args: dict[str, Any]) -> bool:
        self.__init_cycle_set()
        fp = self._fingerprint(name, args)
        return fp in self._seen_calls

    def mark_call(self, name: str, args: dict[str, Any]) -> None:
        self.__init_cycle_set()
        self._seen_calls.add(self._fingerprint(name, args))
