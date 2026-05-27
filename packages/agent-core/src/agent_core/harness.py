"""The agent harness — a LangGraph StateGraph that both agents compile.

Architecture
------------
                      ┌─────────────────────────────────────┐
          user query  │              StateGraph               │  AgentResult
         ────────────►│                                       │─────────────►
                      │  [start] ──► [reason] ──┐            │
                      │                         │ tool calls  │
                      │             [tools]  ◄──┘            │
                      │                │                      │
                      │                └──────────────► [end] │
                      └─────────────────────────────────────┘

Nodes
-----
  reason   Calls the LLM with bound tools.  Checks loop guardrails first.
           If limits are hit, injects a force-summarise user message instead
           of another LLM call with tools.

  tools    Dispatches all tool calls from the last assistant message.
           Runs them in parallel (ThreadPoolExecutor) when the config allows.
           Applies per-tool guardrails before each execution.
           Returns ToolMessages that LangGraph appends to the message list.

Edges
-----
  start ──► reason
  reason ──► tools   (when the LLM returned tool calls)
  reason ──► end     (when the LLM returned a plain text answer)
  tools  ──► reason  (always — agent re-evaluates after seeing results)

The graph is stateless at construction time.  All mutable data lives in
``HarnessState`` which is threaded through by LangGraph.
"""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from research_core.utils.logger import get_logger

from .guards import (
    InputGuardResult,
    check_input,
    check_loop_limits,
    check_output,
    check_tool_allowed,
)
from .models import (
    AgentContext,
    AgentResult,
    HarnessConfig,
    HarnessState,
    TokenUsage,
    ToolCallRecord,
    TraceStep,
)
from .tool import AgentTool, ToolRegistry

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Node: reason
# ---------------------------------------------------------------------------


def _make_reason_node(llm: ChatOpenAI, registry: ToolRegistry, config: HarnessConfig):
    """Return a LangGraph node function that calls the LLM and checks loop limits."""

    # LangChain handles schema conversion when you pass tool instances directly
    llm_with_tools = llm.bind_tools(registry.all()) if registry.all() else llm

    def reason(state: HarnessState) -> dict[str, Any]:
        trace = list(state["trace"])
        warnings = list(state["warnings"])

        # ── Layer 3: loop guards ──────────────────────────────────────────
        guard = check_loop_limits(state)
        if guard.should_stop:
            logger.warning("Loop guard triggered | reason=%s", guard.reason)
            warnings.append(guard.reason)
            trace.append(
                TraceStep(
                    type="guardrail",
                    name="loop_guard",
                    status="completed",
                    data={"reason": guard.reason, "termination": guard.termination_reason},
                )
            )
            # Ask the model to summarise with whatever it has — no more tools
            force_messages = list(state["messages"]) + [
                {
                    "role": "user",
                    "content": (
                        "You have reached your operational limit for this session. "
                        "Based on everything gathered so far, give your best answer now. "
                        "Be transparent about any gaps."
                    ),
                }
            ]
            t0 = time.monotonic()
            try:
                response = llm.invoke(force_messages)
                duration = int((time.monotonic() - t0) * 1000)
                usage_data = getattr(response, "response_metadata", {}).get("token_usage", {})
                new_usage = state["usage"] + TokenUsage(
                    prompt_tokens=usage_data.get("prompt_tokens", 0),
                    completion_tokens=usage_data.get("completion_tokens", 0),
                    total_tokens=usage_data.get("total_tokens", 0),
                )
                trace.append(
                    TraceStep(
                        type="reasoning",
                        name="force_summarise",
                        status="completed",
                        data={"content": getattr(response, "content", "")[:300]},
                        duration_ms=duration,
                    )
                )
                return {
                    "messages": [response],
                    "trace": trace,
                    "warnings": warnings,
                    "usage": new_usage,
                    "termination_reason": guard.termination_reason,
                }
            except Exception as exc:
                logger.error("Force-summarise LLM call failed | error=%s", exc)
                return {
                    "messages": [
                        AIMessage(
                            content="I was unable to complete my analysis within the allowed limits. "
                            "Please try again with a more specific question."
                        )
                    ],
                    "trace": trace,
                    "warnings": warnings,
                    "termination_reason": "error",
                }

        # ── Normal reasoning turn ─────────────────────────────────────────
        t0 = time.monotonic()
        iteration = state["iteration"]
        try:
            response = llm_with_tools.invoke(state["messages"])
            duration = int((time.monotonic() - t0) * 1000)
        except Exception as exc:
            logger.error("LLM call failed | iteration=%d error=%s", iteration, exc)
            trace.append(
                TraceStep(
                    type="system",
                    name="llm_error",
                    status="failed",
                    data={"error": str(exc), "iteration": iteration},
                )
            )
            return {
                "messages": [AIMessage(content=f"I encountered an error: {exc}")],
                "trace": trace,
                "warnings": warnings,
                "termination_reason": "error",
            }

        # Accumulate token usage
        usage_data = getattr(response, "response_metadata", {}).get("token_usage", {})
        new_usage = state["usage"] + TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )
        tool_calls_requested = len(getattr(response, "tool_calls", None) or [])
        trace.append(
            TraceStep(
                type="reasoning",
                name=f"iteration_{iteration}",
                status="completed",
                data={
                    "tool_calls_requested": tool_calls_requested,
                    "content_preview": (getattr(response, "content", "") or "")[:200],
                    "tokens_this_turn": usage_data.get("total_tokens", 0),
                },
                duration_ms=duration,
            )
        )
        logger.debug(
            "Reasoning complete | iteration=%d tool_calls=%d tokens=%d",
            iteration,
            tool_calls_requested,
            usage_data.get("total_tokens", 0),
        )

        return {
            "messages": [response],
            "trace": trace,
            "usage": new_usage,
            "warnings": warnings,
            "iteration": iteration + 1,
            "termination_reason": "done" if not tool_calls_requested else state["termination_reason"],
        }

    return reason


# ---------------------------------------------------------------------------
# Node: tools
# ---------------------------------------------------------------------------


def _make_tool_node(registry: ToolRegistry, config: HarnessConfig):
    """Return a LangGraph node function that dispatches tool calls with guardrails."""

    def tools(state: HarnessState) -> dict[str, Any]:
        last_message = state["messages"][-1]
        raw_tool_calls = getattr(last_message, "tool_calls", None) or []
        if not raw_tool_calls:
            return {}

        trace = list(state["trace"])
        tool_call_records = list(state["tool_calls"])
        warnings = list(state["warnings"])
        context: AgentContext = state["context"]

        def _dispatch_one(tc: dict[str, Any]) -> tuple[ToolMessage, ToolCallRecord, list[TraceStep]]:
            call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            tool_name = tc.get("name", "")
            args: dict[str, Any] = tc.get("args") or {}
            local_traces: list[TraceStep] = []
            record = ToolCallRecord(call_id=call_id, tool_name=tool_name, arguments=args)

            # ── Layer 2: tool guard ───────────────────────────────────────
            veto = check_tool_allowed(tool_name, args, registry, context)
            if veto:
                record.skipped = True
                record.skip_reason = veto
                warnings.append(f"Tool '{tool_name}' skipped: {veto}")
                local_traces.append(
                    TraceStep(
                        type="guardrail",
                        name=f"tool_guard:{tool_name}",
                        status="skipped",
                        data={"call_id": call_id, "reason": veto},
                    )
                )
                return (
                    ToolMessage(content=f"[Skipped] {veto}", tool_call_id=call_id),
                    record,
                    local_traces,
                )

            tool = registry.get(tool_name)
            if tool is None:
                msg = f"Unknown tool '{tool_name}'."
                record.error = msg
                local_traces.append(
                    TraceStep(
                        type="tool_call",
                        name=tool_name,
                        status="failed",
                        data={"call_id": call_id, "error": msg},
                    )
                )
                return ToolMessage(content=f"[Error] {msg}", tool_call_id=call_id), record, local_traces

            local_traces.append(
                TraceStep(
                    type="tool_call",
                    name=tool_name,
                    status="started",
                    data={"call_id": call_id, "args": args},
                )
            )

            # ── Execute ───────────────────────────────────────────────────
            t0 = time.monotonic()
            try:
                result = tool.invoke(args)
                duration = int((time.monotonic() - t0) * 1000)
                registry.record_call(tool_name)
                registry.mark_call(tool_name, args)
                record.duration_ms = duration
                record.result_summary = str(result)[:300]
                content = result if isinstance(result, str) else json.dumps(result, default=str)
                local_traces.append(
                    TraceStep(
                        type="tool_result",
                        name=tool_name,
                        status="completed",
                        data={"call_id": call_id, "summary": record.result_summary, "duration_ms": duration},
                        duration_ms=duration,
                    )
                )
                logger.debug("Tool completed | name=%s duration_ms=%d", tool_name, duration)
            except Exception as exc:
                duration = int((time.monotonic() - t0) * 1000)
                logger.error("Tool failed | name=%s error=%s", tool_name, exc)
                record.error = str(exc)
                record.duration_ms = duration
                content = f"[Error] Tool '{tool_name}' failed: {exc}"
                local_traces.append(
                    TraceStep(
                        type="tool_result",
                        name=tool_name,
                        status="failed",
                        data={"call_id": call_id, "error": str(exc), "duration_ms": duration},
                        duration_ms=duration,
                    )
                )

            return ToolMessage(content=content, tool_call_id=call_id), record, local_traces

        # ── Parallel or serial dispatch ───────────────────────────────────
        tool_messages: list[ToolMessage] = []
        if config.allow_parallel_tools and len(raw_tool_calls) > 1:
            ordered: dict[str, tuple[ToolMessage, ToolCallRecord, list[TraceStep]]] = {
                tc.get("id", ""): None for tc in raw_tool_calls  # type: ignore[assignment]
            }
            with ThreadPoolExecutor(max_workers=len(raw_tool_calls)) as executor:
                future_to_id = {executor.submit(_dispatch_one, tc): tc.get("id", "") for tc in raw_tool_calls}
                for future in as_completed(future_to_id):
                    cid = future_to_id[future]
                    ordered[cid] = future.result()
            for cid in ordered:
                tm, rec, steps = ordered[cid]
                tool_messages.append(tm)
                tool_call_records.append(rec)
                trace.extend(steps)
        else:
            for tc in raw_tool_calls:
                tm, rec, steps = _dispatch_one(tc)
                tool_messages.append(tm)
                tool_call_records.append(rec)
                trace.extend(steps)

        return {
            "messages": tool_messages,
            "trace": trace,
            "tool_calls": tool_call_records,
            "tool_call_count": state["tool_call_count"] + len(raw_tool_calls),
            "warnings": warnings,
        }

    return tools


# ---------------------------------------------------------------------------
# Routing edge: after reasoning, go to tools or end
# ---------------------------------------------------------------------------


def _should_use_tools(state: HarnessState) -> str:
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None)
    if tool_calls:
        return "tools"
    return END


# ---------------------------------------------------------------------------
# Public API: AgentHarness
# ---------------------------------------------------------------------------


class AgentHarness:
    """Compiles and runs the agent graph.

    Usage::

        harness = AgentHarness(tools=[SearchMemoryTool(), ...], config=cfg)
        result = harness.run(
            system_prompt=ADVISOR_SYSTEM_PROMPT,
            user_message="Is NVDA still a good hold?",
            context=AgentContext(user_id="u123", run_id="r456"),
        )
        print(result.answer)
    """

    def __init__(
        self,
        tools: list[AgentTool],
        config: HarnessConfig | None = None,
    ) -> None:
        self.config = config or HarnessConfig()
        self.tools = tools
        self._graph = self._compile(tools, self.config)

    def _compile(self, tools: list[AgentTool], config: HarnessConfig):
        llm = ChatOpenAI(model=config.model, temperature=config.temperature)
        registry = ToolRegistry(tools)

        reason_node = _make_reason_node(llm, registry, config)
        tool_node = _make_tool_node(registry, config)

        graph = StateGraph(HarnessState)
        graph.add_node("reason", reason_node)
        graph.add_node("tools", tool_node)
        graph.set_entry_point("reason")
        graph.add_conditional_edges("reason", _should_use_tools)
        graph.add_edge("tools", "reason")

        return graph.compile()

    def run(
        self,
        *,
        system_prompt: str,
        user_message: str,
        context: AgentContext,
    ) -> AgentResult:
        # ── Layer 1: input guard ──────────────────────────────────────────
        input_check: InputGuardResult = check_input(user_message, context)
        if not input_check.ok:
            return AgentResult(
                answer=f"I can't process that request: {input_check.reason}",
                termination_reason="error",
                warnings=[input_check.reason],
            )

        # Inject context into every tool so they can access user_id etc.
        for tool in self.tools:
            tool.set_context(context)

        initial_state: HarnessState = {
            "messages": [
                SystemMessage(content=system_prompt),
                {"role": "user", "content": user_message},
            ],
            "trace": [
                TraceStep(
                    type="system",
                    name="harness_start",
                    status="completed",
                    data={
                        "run_id": context.run_id,
                        "user_id": context.user_id,
                        "model": self.config.model,
                        "max_iterations": self.config.max_iterations,
                    },
                )
            ],
            "tool_calls": [],
            "usage": TokenUsage(),
            "warnings": [],
            "tool_call_count": 0,
            "iteration": 0,
            "start_time": time.monotonic(),
            "termination_reason": "done",
            "config": self.config,
            "context": context,
        }

        logger.info(
            "Agent run started | run_id=%s user_id=%s model=%s",
            context.run_id,
            context.user_id,
            self.config.model,
        )

        final_state: HarnessState = self._graph.invoke(initial_state)

        # Extract the last assistant message
        answer = ""
        for msg in reversed(final_state["messages"]):
            role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else None)
            content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
            if role in ("ai", "assistant") and content:
                answer = content
                break

        # ── Layer 4: output guard ─────────────────────────────────────────
        output = check_output(answer, final_state["warnings"])

        logger.info(
            "Agent run finished | run_id=%s reason=%s tool_calls=%d tokens=%d",
            context.run_id,
            final_state["termination_reason"],
            final_state["tool_call_count"],
            final_state["usage"].total_tokens,
        )

        return AgentResult(
            answer=output.answer,
            trace=final_state["trace"],
            tool_calls=final_state["tool_calls"],
            usage=final_state["usage"],
            termination_reason=final_state["termination_reason"],  # type: ignore[arg-type]
            warnings=output.warnings,
            metadata={"agentState": context.state},
        )
