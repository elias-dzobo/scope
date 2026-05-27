# Agentic Research Architecture

This note maps Scope from a fixed stock-research pipeline into a deep research agent harness. The goal is not to discard the current deterministic pipeline. The goal is to keep its useful production properties while adding planning, feedback loops, tool choice, and extensibility for company, industry, and personalized portfolio research.

## Why Change

The current fallback pipeline in `packages/research-core/src/research_core/legacy_pipeline/main.py` is a deterministic conveyor belt:

1. prepare artifacts
2. generate pillar query plan
3. discover primary sources
4. search
5. filter
6. scrape
7. extract evidence
8. assess pillars
9. build scorecard

This is good for predictable phase-one company research. It is less good when the task shape changes. Industry research, opportunity discovery, portfolio-aware personalization, and iterative research all need the system to decide what to do next based on what it learned.

Anthropic's distinction is useful here: workflows orchestrate LLMs and tools through predefined code paths; agents dynamically direct their own process and tool usage. Scope should become a hybrid: deterministic workflows for known subtasks, controlled by an agent harness that can plan, inspect quality, retry, branch, and stop.

## Reference Takeaways

The workshop reference repo at `/Users/eliasdzobo/Desktop/2026/designing-real-world-ai-agents-workshop` is small but directionally right:

- It exposes capabilities as tools instead of baking every step into one pipeline.
- It keeps tool implementations thin: `routers -> tools -> app -> utils`.
- Tools return immediate results to the agent and also persist session memory under `.memory/`.
- The agent, not the server, decides which research query to run next.
- Research outputs can be compiled after multiple tool calls.

For Scope, the same idea should be upgraded from a generic MCP research server into a domain-specific financial research harness.

## Target Shape

```text
User request
    |
    v
Research Run API
    |
    v
Research Harness
    |
    +-- task router
    +-- planner
    +-- context builder
    +-- tool executor
    +-- quality gates
    +-- reflection / retry policy
    +-- artifact + memory store
    +-- evaluator hooks
    |
    v
Domain tools
    |
    +-- search tools
    +-- source discovery tools
    +-- scrape / parse tools
    +-- evidence extraction tools
    +-- financial analytics tools
    +-- scoring tools
    +-- writing / report tools
    +-- personalization tools
```

The harness becomes the core product. The model is one component inside it.

## Core Components

### 1. Task Router

Classifies the user request into a research mode:

- `company_equity_research`: current six-pillar company analysis
- `industry_opportunity_scan`: research an industry and surface investable opportunities
- `company_comparison`: compare multiple companies or a basket
- `portfolio_personalized_research`: adapt analysis to a user's holdings, risk profile, liquidity needs, and goals
- `open_ended_research`: exploratory request that needs dynamic planning

This router can start as a structured LLM call plus deterministic checks. It should produce a `ResearchBrief`.

### 2. Research Planner

Turns the brief into a mutable plan:

```json
{
  "objective": "Research Microsoft under the six-pillar framework",
  "mode": "company_equity_research",
  "entities": [{"type": "company", "name": "Microsoft", "ticker": "MSFT"}],
  "workstreams": [
    {
      "id": "financial_engine",
      "goal": "Assess revenue quality, margins, cash flow, and leverage",
      "required_evidence": ["recent revenue", "margin trend", "cash flow", "balance sheet"],
      "status": "pending"
    }
  ],
  "stop_conditions": {
    "min_sources_per_pillar": 3,
    "min_primary_sources_per_core_pillar": 1,
    "max_iterations": 4,
    "confidence_threshold": 0.7
  }
}
```

The plan is not just a pretty artifact. It drives tool selection, progress UI, quality gates, and eval traces.

### 3. Context Builder

Context engineering is the practice of choosing the smallest high-signal context needed for the next model decision. Scope should avoid stuffing the whole run into every prompt.

Use layered context:

- `Run brief`: user request, mode, entities, constraints
- `Plan state`: current workstreams, completed steps, open gaps
- `Evidence index`: compact list of facts, sources, confidence, and source ids
- `Tool catalog`: only the tools relevant to the next decision
- `Retrieved artifacts`: just-in-time source chunks, not entire scraped bodies
- `User profile`: only for phase three personalization and only the fields relevant to the decision

This matches the reference repo's `.memory/` pattern, but with structured financial state instead of only markdown and raw JSON.

### 4. Tool Layer

Turn today's pipeline stages into agent-callable tools. Initial tool groups:

Search:

- `generate_queries`
- `run_grounded_search`
- `run_search_batch`
- `discover_primary_sources`
- `search_company_filings`
- `search_industry_reports`

Retrieval:

- `scrape_source`
- `parse_pdf`
- `extract_tables`
- `dedupe_sources`
- `rank_sources`

Evidence:

- `extract_evidence`
- `map_evidence_to_pillar`
- `check_evidence_coverage`
- `find_evidence_gaps`

Analytics:

- `assess_pillar`
- `build_scorecard`
- `compare_companies`
- `screen_industry_opportunities`
- `estimate_valuation_inputs`

Writing:

- `write_research_memo`
- `write_pillar_section`
- `write_source_library`
- `write_personalized_takeaway`

The tools need agent-friendly contracts: clear names, minimal overlap, structured inputs, structured outputs, and compact responses. Raw source bodies should be saved as artifacts and returned by reference unless the agent specifically asks for content.

### 5. Quality Gates

Every major stage should produce a scorecard that can trigger another loop.

Examples:

- Search quality gate:
  - enough sources?
  - enough primary sources?
  - source freshness acceptable?
  - source diversity acceptable?

- Evidence quality gate:
  - enough facts per pillar?
  - facts mention the target company?
  - facts include metrics where required?
  - unsupported claims detected?

- Analysis quality gate:
  - does each score cite evidence?
  - are confidence and recommendation aligned?
  - are valuation and technical claims backed by specific data?

- Writing quality gate:
  - source-grounded?
  - no unsupported investment claims?
  - clear thesis, risks, invalidation conditions?

If a gate fails, the harness asks: retry search, change query, switch source type, ask user, or mark insufficient data.

### 6. Reflection Loop

The loop should be explicit and bounded:

```text
plan -> act -> observe -> grade -> decide
```

`decide` can return:

- `continue`: enough signal, move to next workstream
- `retry`: same goal, better query/source/tool
- `branch`: create a new subtask
- `escalate`: ask user for clarification or permission
- `stop`: compile final answer with limitations

This gives us the feedback loop that the current pipeline lacks without making the system infinitely autonomous.

### 7. Memory And Artifacts

Keep two stores:

- Durable run artifacts: raw sources, scraped documents, extracted tables, evidence, assessments, report
- Agent memory: plan state, decisions, observations, gap lists, compact summaries

Suggested structure:

```text
artifacts/{run_id}/
  brief.json
  plan.json
  trace.jsonl
  memory/
    notes.md
    open_questions.json
    gap_report.json
  sources/
    source_index.json
    raw/
    parsed/
  evidence/
    facts.json
    coverage.json
  analysis/
    pillar_assessments.json
    scorecard.json
  report/
    research_memo.md
```

This also makes long-running context manageable: the model reads compact summaries and pulls detailed artifacts only when needed.

## Agent Roles

Start with one lead agent plus deterministic tools. Add specialist agents only when they are useful.

Phase one:

- `ResearchController`: owns plan, tool selection, and loop decisions
- deterministic tools: search, scrape, filter, extract, score
- LLM calls: planning, gap analysis, evidence extraction, narrative synthesis

Phase two:

- `SearchAgent`: explores industries, companies, and source maps
- `AnalystAgent`: turns evidence into financial/strategic judgments
- `WriterAgent`: produces the final memo
- `VerifierAgent`: checks source grounding, gaps, and contradictions

Phase three:

- `PersonalizationAgent`: maps research to user goals, portfolio exposure, risk tolerance, and constraints

The separation should be logical first. Do not force a multi-agent runtime before the single-controller harness has good traces and evals.

## How The Current Code Maps

Keep:

- `apps/api/src/scope_api/application/run_service.py`: run lifecycle and API orchestration
- `apps/api/src/scope_api/orchestration.py`: bounded queue, eventually replace with durable jobs
- `packages/provider-integrations/src/provider_integrations/search/main.py`: provider abstraction
- `packages/provider-integrations/src/provider_integrations/tools/main.py`: search/scrape/query tools, split into smaller modules later
- `packages/research-core/src/research_core/scoring/main.py`: evidence and scoring logic
- `apps/api/src/scope_api/observability/*`: metrics and traces
- `storage/artifacts/*/_pipeline_state`: evolve into run memory/checkpoints

Change:

- `stock_research_pipeline(...)` becomes a compatibility wrapper around the harness for `company_equity_research`.
- Each `_stage_*` function becomes a tool or workflow callable by the harness.
- `selected_pillars` becomes part of `ResearchBrief.constraints`.
- Progress events should come from plan/workstream state, not fixed pipeline stage names only.

Add:

- `src/harness/brief.py`
- `src/harness/planner.py`
- `src/harness/context.py`
- `src/harness/controller.py`
- `src/harness/gates.py`
- `src/harness/memory.py`
- `src/harness/tools.py`
- `src/evals/research_harness.py`

## Phase Roadmap

### Phase 1: Agentic Company Research

Purpose: keep the six-pillar product, add feedback loops.

Build:

- `ResearchBrief`
- mutable `ResearchPlan`
- `ResearchController`
- tool registry wrapping current stage functions
- source/evidence coverage gates
- retry on weak pillars
- final report compiler

Success criteria:

- weak source coverage triggers additional search before scoring
- insufficient evidence is explicitly marked rather than silently scored
- every pillar score can be traced to evidence facts and sources
- current API and UI still work

### Phase 2: Industry Opportunity Research

Purpose: support requests like "research the obesity drug market and find opportunities."

Build:

- industry-mode router
- industry source discovery
- opportunity screening tool
- company candidate extraction
- comparison workflow

Likely flow:

```text
industry thesis -> market map -> trend drivers -> company universe -> screen candidates -> deep-dive selected companies -> report opportunities
```

### Phase 3: Personalized Research

Purpose: adapt outputs to a specific investor.

Build:

- user profile schema
- portfolio context schema
- suitability/risk gate
- personalization layer separate from base research

Important boundary: the base company or industry analysis should remain objective. Personalization should be a final layer that says how the research maps to the user's goals and constraints.

## Evaluation Harness

Agent production quality needs evals at three levels:

- Unit/tool evals: did the tool return the right shape, source type, and compact context?
- Trajectory evals: did the agent choose a reasonable sequence of actions, avoid loops, and use primary sources?
- Outcome evals: was the final memo comprehensive, grounded, and decision-useful?

Initial eval cases:

- Known company with strong public sources, e.g. MSFT
- Company with sparse sources, e.g. Ghana-listed or regional equities
- Industry scan with no single ticker
- Deliberately ambiguous request requiring clarification
- Portfolio-personalized request with risk constraints

Metrics:

- pillar coverage
- primary-source coverage
- unsupported-claim rate
- source freshness
- cost per completed run
- latency per workstream
- loop count
- human override rate
- final report quality rubric

## Implementation Principles

- Keep deterministic logic where it is strong.
- Let the model decide only where flexibility is valuable.
- Treat context as a scarce resource.
- Return compact tool outputs and persist heavy artifacts by reference.
- Use bounded loops with explicit stop conditions.
- Make every agent decision traceable.
- Prefer one controller plus tools before adding multiple autonomous agents.
- Build evals from real failures as soon as the first harness loop exists.

## External References

- Anthropic, "Building effective agents": workflows vs agents, simple composable patterns, and agent-computer interface guidance.
- Anthropic, "Effective context engineering for AI agents": context as finite attention budget, just-in-time retrieval, compaction, notes, and sub-agent architectures.
- Anthropic, "Writing effective tools for AI agents": tool contracts, namespacing, token-efficient responses, and tool evals.
- Anthropic, "Demystifying evals for AI agents": evaluation harnesses, task/trial/transcript/outcome definitions, and research-agent eval strategies.
- Braintrust, "What is agent evaluation?": eval harness structure across datasets, task runner, scoring, aggregation, CI, and feedback loops.
- AWS, "Evaluating AI agents for production": production eval framing for non-deterministic agent systems.
- Workshop repo: `src/research/README.md`, `src/research/tools/deep_research_tool.py`, `src/research/tools/compile_research_tool.py`, and `src/writing/evals/evaluation.py`.
