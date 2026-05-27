"""Tool registry for the agentic research loop.

The registry maps tool names to callable AgentTool instances. The
ResearchAgentLoop resolves tools by name at runtime via registry.get(name),
so new tools can be registered without changing loop logic.

Usage
-----
    registry = ToolRegistry(facade, gates)
    result = registry.get("grounded_search").run(ctx)
    registry.register(MyCustomTool(facade))   # extend without subclassing
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from research_core.harness.agent_tools import (
    AgentTool,
    DocumentDiscoveryTool,
    DocumentFetchTool,
    DocumentParseTool,
    EvidenceExtractionTool,
    EvidenceQualityTool,
    GroundedSearchTool,
    PillarScoringTool,
    QueryGenerationTool,
    QueryRefinementTool,
    SynthesisTool,
    WebSearchTool,
)

if TYPE_CHECKING:
    from research_core.harness.tools import ResearchToolFacade
    from research_core.harness.gates import ResearchQualityGates


class ToolRegistry:
    """Maps tool names to AgentTool instances.

    The full default set covers every stage of the agentic loop:

    ┌─────────────────────┬──────────────────────────────────────────────────┐
    │ Tool name           │ Purpose                                          │
    ├─────────────────────┼──────────────────────────────────────────────────┤
    │ query_generation    │ Build Gemini prompt for a workstream             │
    │ grounded_search     │ Gemini grounded web search                       │
    │ web_search          │ Direct provider search (fallback / supplement)   │
    │ document_discovery  │ Classify and rank document candidates            │
    │ document_fetch      │ Download one document (PDF / HTML)               │
    │ document_parse      │ Parse document into text + tables                │
    │ evidence_extraction │ Extract pillar facts from a parsed document      │
    │ query_refinement    │ Derive better queries from evidence gaps ★       │
    │ evidence_quality    │ Run quality gates on assembled evidence          │
    │ pillar_scoring      │ Assess pillars + build scorecard                 │
    │ synthesis           │ Generate final investment memo                   │
    └─────────────────────┴──────────────────────────────────────────────────┘

    ★ = the core agentic tool — enables the retry loop
    """

    def __init__(
        self,
        facade: "ResearchToolFacade",
        gates: "ResearchQualityGates",
    ) -> None:
        self._registry: dict[str, AgentTool] = {
            "query_generation": QueryGenerationTool(facade),
            "grounded_search": GroundedSearchTool(facade),
            "web_search": WebSearchTool(facade),
            "document_discovery": DocumentDiscoveryTool(facade),
            "document_fetch": DocumentFetchTool(facade),
            "document_parse": DocumentParseTool(facade),
            "evidence_extraction": EvidenceExtractionTool(facade),
            "query_refinement": QueryRefinementTool(facade),
            "evidence_quality": EvidenceQualityTool(facade, gates),
            "pillar_scoring": PillarScoringTool(facade),
            "synthesis": SynthesisTool(facade),
        }

    def get(self, name: str) -> AgentTool:
        """Return a tool by name.

        Raises ``KeyError`` for unknown tools so callers fail fast rather than
        silently falling back to a wrong tool.
        """
        try:
            return self._registry[name]
        except KeyError:
            available = ", ".join(sorted(self._registry))
            raise KeyError(
                f"Unknown tool '{name}'. Available: {available}"
            ) from None

    def register(self, tool: AgentTool) -> None:
        """Register or replace a tool.

        Custom tools must satisfy the AgentTool protocol (name, description,
        and a run(ctx, **kwargs) → ToolResult method).
        """
        self._registry[tool.name] = tool

    @property
    def available_tools(self) -> list[str]:
        """Sorted list of registered tool names."""
        return sorted(self._registry.keys())

    def __repr__(self) -> str:
        return f"ToolRegistry(tools={self.available_tools})"
