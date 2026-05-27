"""agent_core — shared agentic harness for Scope's advisor and research agents.

Public surface::

    from agent_core.harness import AgentHarness
    from agent_core.models import AgentContext, AgentResult, HarnessConfig
    from agent_core.tool import AgentTool
"""

from .harness import AgentHarness
from .models import AgentContext, AgentResult, HarnessConfig, TraceStep, TokenUsage
from .tool import AgentTool, ToolRegistry

__all__ = [
    "AgentHarness",
    "AgentContext",
    "AgentResult",
    "HarnessConfig",
    "TraceStep",
    "TokenUsage",
    "AgentTool",
    "ToolRegistry",
]
