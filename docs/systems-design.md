# Scope — Systems Design Overview

## What It Is

Scope is a stock research orchestrator. A user submits a company name and ticker; the system
runs a multi-stage AI pipeline across six equity due diligence pillars and returns a structured
investment memo with evidence citations, a scorecard, and a personalized recommendation.

---

## High-Level Architecture

```mermaid
graph TD
    subgraph Browser["Browser"]
        UI["React SPA<br/>(Vite + TypeScript)"]
    end

    subgraph FlyWeb["Fly.io — web process"]
        API["FastAPI<br/>api_main.py"]
        Auth["Google OAuth<br/>+ JWT Auth"]
        Orch["In-Process Orchestrator<br/>(private beta)"]
        AdvisorAPI["Advisor Endpoints"]
        MemoryAPI["GraphRAG Memory"]
    end

    subgraph FlyWorker["Fly.io — worker process"]
        Worker["Durable Research Worker<br/>scope_api.worker"]
    end

    subgraph ResearchCore["packages/research-core"]
        Controller["ResearchController"]
        Planner["ResearchPlanner"]
        Runner["CompanyResearchRunner"]
        Tools["ResearchToolFacade"]
        Gates["Quality Gates"]
        Synthesis["FinalSynthesisGenerator"]
    end

    subgraph ProviderIntegrations["packages/provider-integrations"]
        Search["Search Providers<br/>Exa → Tavily → SerpAPI → DDG"]
        DocParser["Document Parser<br/>PDF + HTML + Tables"]
    end

    subgraph External["External Services"]
        Gemini["Google Gemini 2.5 Flash<br/>(grounded web search)"]
        OpenAI["OpenAI gpt-4o-mini<br/>(planning + extraction)"]
        GoogleOAuth["Google OAuth<br/>(identity)"]
        SearchAPIs["Search APIs<br/>(Exa / Tavily / SerpAPI)"]
        PublicWeb["Public Web<br/>(annual reports, filings)"]
    end

    subgraph Data["Data Layer"]
        Neon["Neon Postgres"]
        Tigris["Tigris / R2<br/>(artifact storage)"]
    end

    UI -->|"POST /api/v1/research-runs<br/>JWT in header"| API
    UI -->|"Poll GET /research-runs/id<br/>every 3 seconds"| API
    UI -->|"GET /research-runs/id/results"| API
    UI -->|"POST /api/v1/auth/google<br/>Google ID token"| Auth
    UI -->|"POST /api/v1/advisor/..."| AdvisorAPI

    Auth -->|"validate token"| GoogleOAuth
    Auth -->|"write user + session"| Neon

    API -->|"submit job (in-process mode)"| Orch
    Orch -->|"thread pool"| Controller

    API -->|"write queued run"| Neon
    Worker -->|"lease job from DB"| Neon
    Worker -->|"heartbeat every 30s"| Neon
    Worker --> Controller

    Controller --> Planner
    Controller --> Runner
    Runner --> Tools
    Runner --> Gates

    Planner -->|"create workstreams"| OpenAI
    Tools -->|"grounded research<br/>(6x per run, one per pillar)"| Gemini
    Tools -->|"document discovery"| Search
    Search --> SearchAPIs
    Tools -->|"fetch + parse documents"| DocParser
    DocParser --> PublicWeb
    Tools -->|"evidence extraction"| OpenAI
    Gates -->|"LLM alignment judge"| OpenAI
    Runner --> Synthesis
    Synthesis -->|"investment memo"| OpenAI

    Runner -->|"write artifacts"| Tigris
    Runner -->|"update run status + progress"| Neon
    Runner -->|"index user memory graph"| Neon

    AdvisorAPI -->|"retrieve memory context"| MemoryAPI
    MemoryAPI --> Neon
    AdvisorAPI -->|"multi-turn response"| OpenAI
```

---

## Request Lifecycle — "Start Research"

```mermaid
sequenceDiagram
    participant User
    participant Web as React SPA
    participant API as FastAPI
    participant DB as Neon Postgres
    participant Worker as Research Worker
    participant Gemini
    participant Search as Search Providers
    participant OAI as OpenAI

    User->>Web: Submit company + ticker
    Web->>API: POST /api/v1/research-runs (JWT)
    API->>DB: INSERT research_runs (status=queued)
    API-->>Web: 202 { run_id, status: "queued" }

    loop Poll every 3s
        Web->>API: GET /research-runs/{run_id}
        API->>DB: SELECT status, progress, current_stage
        API-->>Web: { status, progress, current_substep }
    end

    Worker->>DB: Lease next queued run (row-level lock)
    DB-->>Worker: run_id, company, ticker, pillars
    Worker->>DB: UPDATE status=running

    Worker->>OAI: Create research plan (6 workstreams)
    OAI-->>Worker: ResearchPlan

    loop For each of 6 pillars (parallel workstreams)
        Worker->>Gemini: Grounded web search (Google Search tool)
        Gemini-->>Worker: sources + citation supports + evidence
    end

    Worker->>Search: Discover primary documents (annual reports, 10-Ks)
    Search-->>Worker: Ranked document URLs

    loop For each document (up to 8)
        Worker->>Worker: Fetch + parse (PDF / HTML)
        Worker->>OAI: Extract pillar-aligned facts
        OAI-->>Worker: Structured evidence facts
    end

    Worker->>Worker: Quality gates (evidence alignment, source count, freshness)
    alt Gate fails
        Worker->>Worker: Targeted fallback (legacy deterministic pipeline)
        Worker->>Worker: Re-evaluate gates
    end

    Worker->>Worker: Score pillars (deterministic)
    Worker->>OAI: Generate final synthesis (investment memo)
    OAI-->>Worker: Narrative + recommendation

    Worker->>DB: UPDATE status=completed, result_json
    Worker->>DB: Insert artifact manifest records
    Worker->>DB: Index user memory graph nodes + edges

    Web->>API: GET /research-runs/{run_id}/results
    API->>DB: SELECT result_json
    API-->>Web: Full result payload
    Web->>User: Render AnalysisDashboard
```

---

## Research Pipeline — Stage by Stage

| # | Stage | What Happens | LLM Used | Fallback |
|---|-------|-------------|----------|---------|
| 1 | **Planning** | Generate 6 workstreams with search focus and required evidence per pillar | GPT-4o-mini | Deterministic fixed plan |
| 2 | **Grounded Research** | Gemini runs Google Search and returns grounded answers with citation supports | Gemini 2.5 Flash | `status=unavailable` if no key |
| 3 | **Document Discovery** | Discover + rank annual reports, 10-Ks, transcripts via search | — | Skip if search fails |
| 4 | **Document Parsing** | Fetch PDFs/HTML, extract text chunks + financial tables | GPT-4o-mini (optional) | Deterministic keyword extraction |
| 5 | **Evidence Assembly** | Merge grounding evidence + document evidence per pillar | — | — |
| 6 | **Quality Gates** | Score alignment, check source count, freshness, primary docs, financial tables | GPT-4o-mini (LLM judge, optional) | Skip judge if no key |
| 7 | **Targeted Fallback** | Run legacy deterministic pipeline for weak pillars only | — | Full legacy pipeline if all pillars weak |
| 8 | **Pillar Scoring** | Score each of 6 pillars + compute overall score and recommendation | — | Always deterministic |
| 9 | **Final Synthesis** | Write investment memo with personalized recommendation | OpenAI | Run fails if synthesis errors |

---

## Data Model

```mermaid
erDiagram
    users {
        uuid id PK
        string google_sub
        string email
        string display_name
        jsonb financial_profile
        jsonb risk_profile
    }

    user_sessions {
        uuid id PK
        uuid user_id FK
        string jwt_token
        timestamp expires_at
    }

    research_runs {
        uuid id PK
        uuid user_id FK
        string company_name
        string ticker
        jsonb selected_pillars
        string status
        string current_stage
        int progress
        jsonb result_json
        jsonb summary_json
        jsonb profile_snapshot_json
        jsonb budget_snapshot_json
        string lease_owner
        timestamp lease_expires_at
        timestamp heartbeat_at
        int retry_count
    }

    run_events {
        uuid id PK
        uuid run_id FK
        string stage
        string status
        jsonb payload_json
        timestamp created_at
    }

    artifact_manifest {
        uuid id PK
        uuid run_id FK
        uuid user_id FK
        string ticker
        string artifact_type
        string storage_backend
        string storage_uri
        int size_bytes
    }

    memory_nodes {
        uuid id PK
        uuid user_id FK
        string node_type
        string title
        string summary
        jsonb properties_json
    }

    memory_edges {
        uuid id PK
        uuid source_node_id FK
        uuid target_node_id FK
        string edge_type
    }

    memory_chunks {
        uuid id PK
        uuid user_id FK
        uuid node_id FK
        string source_type
        string text
    }

    advisor_runs {
        uuid id PK
        uuid user_id FK
        uuid research_run_id FK
    }

    advisor_messages {
        uuid id PK
        uuid run_id FK
        string role
        text content
    }

    users ||--o{ research_runs : "owns"
    users ||--o{ user_sessions : "has"
    users ||--o{ memory_nodes : "has"
    research_runs ||--o{ run_events : "logs"
    research_runs ||--o{ artifact_manifest : "produces"
    memory_nodes ||--o{ memory_edges : "source"
    memory_nodes ||--o{ memory_edges : "target"
    memory_nodes ||--o{ memory_chunks : "has"
    advisor_runs ||--o{ advisor_messages : "contains"
```

---

## Process Groups (Fly.io)

```
┌──────────────────────────────────────────┐
│  Fly app: scope-api                      │
│                                          │
│  ┌──────────────────┐                   │
│  │  web (1x)        │  public HTTP :8000│
│  │  api_main.py     │  health checks    │
│  │  shared-cpu-1x   │  CORS, rate limit │
│  │  1 GB RAM        │  auth + advisor   │
│  └──────────────────┘                   │
│                                          │
│  ┌──────────────────┐                   │
│  │  worker (1x)     │  no public HTTP   │
│  │  scope_api.worker│  leases DB jobs   │
│  │  shared-cpu-2x   │  heartbeat 30s    │
│  │  2 GB RAM        │  restart=always   │
│  └──────────────────┘                   │
└──────────────────────────────────────────┘
         │                    │
         ▼                    ▼
   Neon Postgres        Tigris (S3)
   (pooled conn)        (artifacts)
```

---

## Six Research Pillars

| Pillar | What It Assesses |
|--------|-----------------|
| **Macro & Industry** | Sector tailwinds, competitive dynamics, regulatory environment |
| **Economic Moat** | Durable competitive advantage, switching costs, network effects |
| **Financial Engine** | Revenue, margins, cash flow, balance sheet health |
| **Management & Capital Allocation** | Leadership quality, capital deployment, shareholder returns |
| **Valuation** | Trading multiples, intrinsic value, margin of safety |
| **Technical Analysis** | Price trend, momentum, support/resistance levels |

---

## Advisor (Post-Research)

After a research run completes, users can open an advisor conversation anchored to that run.

```
User message
    ↓
Retrieve memory context
    ├── Graph nodes for this company (memory_nodes WHERE source_ref = run_id)
    ├── Text chunks from evidence (memory_chunks)
    └── Profile snapshot from run
    ↓
Prompt = system context + memory + conversation history + user message
    ↓
OpenAI (GPT-4o or configured model)
    ↓
Response persisted to advisor_messages
    ↓
Returned to user
```

---

## Key Constraints & Limits

| Constraint | Value | Where Configured |
|-----------|-------|-----------------|
| Max active research runs per user | 2 | `RESEARCH_MAX_ACTIVE_RUNS_PER_USER` |
| Daily research limit per user | 10 | `RESEARCH_DAILY_LIMIT_PER_USER` |
| Worker lease duration | 300s | `RESEARCH_LEASE_SECONDS` |
| Heartbeat interval | 30s | `RESEARCH_HEARTBEAT_SECONDS` |
| Max document candidates | 8 | Hard-coded in runner |
| Max evidence facts per document | 24 | Hard-coded in parser |
| Max grounded facts per signal | 6 | Hard-coded in grounding |
| API rate limit | 30 req/min | `RESEARCH_RATE_LIMIT_PER_MIN` |
| Request body max | 26 MB | `SCOPE_MAX_BODY_BYTES` |
| Artifact retention | ephemeral | `ARTIFACT_RETENTION_MODE` |
| Source freshness threshold | 365 days | `SCOPE_RESEARCH_SOURCE_STALE_DAYS` |
