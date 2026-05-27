# Performance Architecture Guide

This document explains how the Scope research pipeline evolved from a straightforward but slow design into a much more efficient retrieval-and-analysis system.

The goal is not only to document what changed in this codebase, but to describe reusable patterns that can be applied to any system that follows a similar shape:

- generate work
- retrieve data
- rank/filter data
- fetch full documents
- extract structured evidence
- score or synthesize

The examples here use Scope's stock-research pipeline, but the same ideas apply to:

- legal research systems
- scientific literature retrieval
- RAG pipelines
- document intelligence systems
- competitive-intelligence agents
- monitoring and alerting pipelines

## 1. The Initial Design

The original pipeline shape was conceptually simple:

1. generate search queries
2. search the web
3. filter search results with an LLM judge
4. scrape every filtered result
5. extract evidence
6. score the pillars

In code, the main flow lives in `src/pipeline/main.py`.

That early design was easy to understand, but it had three major performance problems:

### Problem A: Too much serial work

Several stages processed items one-by-one:

- search queries
- LLM judging
- scraping

Any system that performs network calls or LLM calls sequentially will usually spend most of its wall-clock time waiting.

### Problem B: Expensive reasoning happened too early

The initial system asked the LLM to judge far too many candidate results before doing enough cheap pruning.

That meant we were paying high-latency model cost on documents that could have been rejected deterministically.

### Problem C: Full-document fetching happened on too many sources

After filtering, the scraper would still attempt to fetch a large number of documents. If the fast HTTP path failed, browser automation could take over. That made scraping the dominant runtime cost.

### Problem D: Good sources were discovered by accident rather than by design

For financial research, the best sources are usually:

- investor relations pages
- annual reports
- quarterly results
- investor presentations
- proxy/governance materials
- regulatory filings

The initial design depended too heavily on broad web search to find those sources.

That hurt both:

- speed
- source quality

## 2. Baseline: What The Slow System Looked Like

The first strong baseline came from `artifacts/VST/analysis/runtime_profile.json`, summarized in `docs/performance-baseline.md`.

The important numbers were:

- total runtime: about `57.4 min`
- filter: `46.6%`
- scrape: `42.3%`

Together:

- filter + scrape = `88.9%` of total runtime

This is a common pattern in retrieval systems:

- not much time is spent deciding what to do
- most time is spent doing too much expensive work on too many items

That baseline gave us the right optimization order:

1. reduce candidate count before LLM reasoning
2. parallelize external calls
3. budget full-document fetching
4. intentionally prioritize primary sources

## 3. Design Principles Behind The New Approach

The improved system is based on a few general principles.

### Principle 1: Use cheap filters before expensive reasoning

Do not ask an LLM to inspect everything.

Instead:

1. classify cheaply
2. score cheaply
3. rank cheaply
4. only send the best subset to the model

This is one of the highest-leverage patterns in any LLM pipeline.

### Principle 2: Parallelize independent I/O

If tasks are independent and mostly waiting on external systems, run them concurrently with bounded workers.

Good candidates:

- search queries
- batched LLM judging calls
- page scraping
- primary-source discovery queries

Bad candidates:

- tightly stateful steps
- code paths where ordering changes semantics

### Principle 3: Batch model calls when the task is repetitive

If the model is doing the same decision repeatedly over many small inputs, batch them.

Instead of:

- 1 result => 1 model call

Prefer:

- 6 results => 1 model call

This reduces:

- round-trip overhead
- prompt overhead
- rate-limit pressure
- total wall-clock time

### Principle 4: Spend effort on high-value sources first

A high-quality primary source deserves more fetch effort than a weak general-web source.

That means:

- long timeout or browser fallback for primary sources
- fast-fail policy for weak sources

### Principle 5: Instrument changes so you can see if they actually worked

Performance work without observability becomes guesswork.

You need to know:

- how many candidates were found
- how many were prepared
- how many were judged
- how many were accepted
- how many were scraped
- how many needed browser fallback

That is why we expanded the runtime profile and stage checkpoints.

## 4. What We Changed

## 4.1 Query Planning: Better Inputs, Better Sources

Files:

- `src/tools/main.py`
- `src/prompts/tool_prompts.py`

Originally, query generation depended mostly on an LLM prompt. That gave decent coverage, but not enough control over source quality.

We kept the LLM-generated plan, then deterministically enriched it with higher-signal primary-source queries.

Example pattern from `src/tools/main.py`:

```python
def _enrich_query_plan_with_primary_source_queries(query_plan: dict[str, Any], stock_name: str) -> dict[str, Any]:
    max_queries_per_pillar = 12
    for pillar in query_plan.get("pillars", []):
        existing = [
            {"query": item.get("query", ""), "intent": item.get("intent", "")}
            for item in pillar.get("queries", [])
        ]
        seeded = _build_primary_source_seed_queries(stock_name, pillar.get("pillar_name", ""))
        merged = _dedupe_queries(seeded + existing)
        pillar["queries"] = merged[:max_queries_per_pillar]
    return query_plan
```

Why this helped:

- the query plan became less dependent on prompt quality alone
- IR and filing-style queries became guaranteed, not optional
- financial pillars started from better retrieval intent

General lesson:

- use the model for breadth
- use deterministic augmentation for must-have coverage

## 4.2 Dedicated Primary-Source Discovery

Files:

- `src/utils/utils.py`
- `src/pipeline/main.py`

We added a new stage:

- `02b_primary_source_discovery`

Its job is to run a small number of deterministic high-signal searches for:

- investor relations home page
- annual reports
- quarterly results
- earnings presentations
- proxy/governance docs
- filings
- transcripts

Example structure from `src/utils/utils.py`:

```python
def _build_primary_discovery_queries(stock_name: str, ticker: str = "") -> list[dict[str, Any]]:
    quoted = f"\"{stock_name}\""
    ticker_part = f" {ticker}" if ticker.strip() else ""
    return [
        {
            "discovery_type": "investor_relations_home",
            "pillar_candidates": ["Financial Engine", "Management & Capital Allocation", "Valuation", "Economic Moat"],
            "query": f"{quoted}{ticker_part} investor relations",
            "intent": "Find the company investor-relations home page.",
        },
        ...
    ]
```

Then those results are:

- classified
- scored
- deduped
- mapped to pillars
- merged into the ordinary candidate pool

Why this helped:

- important sources are discovered intentionally
- the system does not have to "get lucky" through general web search
- primary sources become first-class candidates

General lesson:

If your domain has canonical high-value sources, create a dedicated discovery stage for them.

Do not rely on generic retrieval alone.

## 4.3 Parallel Search Execution

File:

- `src/utils/utils.py`

The old search loop was effectively serial. We replaced it with bounded concurrency.

Example pattern:

```python
with ThreadPoolExecutor(max_workers=max(1, min(SEARCH_MAX_WORKERS, total_queries or 1))) as executor:
    futures = [executor.submit(run_query, task) for task in tasks]
    for future in as_completed(futures):
        pillar_name, _, result_item = future.result()
        search_results[pillar_name].append(result_item)
```

Important details:

- `max_workers` is bounded
- results are re-sorted afterward to preserve query order
- progress updates are still emitted

Why this helped:

- search is I/O-bound
- each query is independent
- concurrency dramatically reduces wall-clock time

General lesson:

If each search call is independent, parallelize it. Keep the worker count bounded and restore deterministic ordering after collection.

## 4.4 Deterministic Pre-Filter Before LLM Judging

File:

- `src/utils/utils.py`

This was one of the biggest changes.

Instead of judging all candidate search results with the LLM, we now:

1. classify candidates
2. score them deterministically
3. keep only top-N per query
4. keep only top-N per pillar
5. only then send them to the LLM

The scoring logic uses:

- entity match
- pillar keyword match
- source quality
- document type
- freshness hints
- low-signal penalties

Example shape:

```python
def _prepare_filter_candidates(
    pillar: str,
    values: list[dict[str, Any]],
    entity_terms: set[str],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for batch in values:
        ...
        batch_ranked.sort(key=lambda item: item["deterministic_score"], reverse=True)
        ranked.extend(batch_ranked[:FILTER_TOP_PER_QUERY])

    deduped: list[dict[str, Any]] = []
    ...
    for candidate in sorted(ranked, key=lambda item: item["deterministic_score"], reverse=True):
        ...
        if len(deduped) >= FILTER_TOP_PER_PILLAR:
            break
```

Why this helped:

- the LLM no longer sees the long tail of weak candidates
- relevance work moves from expensive reasoning to cheap heuristics
- candidate count becomes controllable

General lesson:

Always ask:

- what can be rejected cheaply?

That cheap rejection layer is often the most valuable optimization in the system.

## 4.5 Batch LLM Judging

Files:

- `src/tools/main.py`
- `src/prompts/tool_prompts.py`
- `src/utils/utils.py`

The early shape of the filter was "one candidate, one model call".

That is almost always too slow.

We replaced it with batched judging.

Prompt contract:

- `BATCH_LLM_JUDGE` in `src/prompts/tool_prompts.py`

Caller:

```python
def _judge_candidate_batch(
    pillar: str,
    stock_name: str,
    batch_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    request_payload = [
        {
            "candidate_id": item["candidate_id"],
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "snippet": item.get("snippet", ""),
        }
        for item in batch_items
    ]
    evaluations = llm_as_a_batch_judge(pillar, stock_name, request_payload)
```

And the batched work is itself parallelized:

```python
with ThreadPoolExecutor(max_workers=max(1, FILTER_MAX_WORKERS)) as executor:
    futures = [
        executor.submit(_judge_candidate_batch, pillar, stock_name, batch_items)
        for batch_items in batches
    ]
```

Why this helped:

- fewer model round trips
- less prompt overhead
- much lower filter latency

General lesson:

If your model task is repetitive and independent across items, batch first, parallelize second.

## 4.6 Scrape Budgeting

File:

- `src/utils/utils.py`

The initial pipeline scraped up to 15 documents per pillar. That was too expensive.

We introduced a scrape budget:

- `SCRAPE_TOP_PER_PILLAR`

And we scrape only the highest-priority candidates first.

Example:

```python
prioritized = sorted(
    values,
    key=lambda item: (
        int(bool(item.get("is_primary_source", False))),
        int(item.get("source_trust_score", 0)),
        int(item.get("deterministic_score", 0)),
    ),
    reverse=True,
)[:SCRAPE_TOP_PER_PILLAR]
```

Why this helped:

- fewer full-document fetches
- more of the scrape budget is spent on primary sources
- the system moves from "scrape everything plausible" to "scrape the best subset"

General lesson:

Introduce explicit budgets for expensive stages.

If a stage can expand without limit, it usually will.

## 4.7 Parallel Scraping With Per-Domain Limits

File:

- `src/utils/utils.py`

Scraping is another I/O-bound step, so we parallelized it with bounded workers.

But naive parallel scraping can overload one domain or trigger more failures.

So we also added per-domain concurrency control with `threading.BoundedSemaphore`.

Pattern:

```python
with ThreadPoolExecutor(max_workers=max(1, SCRAPE_MAX_WORKERS)) as executor:
    futures = [executor.submit(scrape_one, pillar, sample) for pillar, sample in tasks]
    for future in as_completed(futures):
        pillar, result_item = future.result()
        results[pillar].append(result_item)
```

Inside each task:

```python
with semaphore_lock:
    semaphore = domain_semaphores.setdefault(
        host,
        threading.BoundedSemaphore(max(1, SCRAPE_DOMAIN_CONCURRENCY)),
    )
with semaphore:
    scraped = scrape_site_detailed(...)
```

Why this helped:

- reduced total wall-clock scrape time
- avoided hammering one host
- kept concurrency high without becoming reckless

General lesson:

For outbound web work:

- global concurrency limit
- per-domain limit

is a strong default design.

## 4.8 Smarter Browser Fallback Policy

Files:

- `src/tools/main.py`
- `src/utils/utils.py`

Selenium or browser-based scraping is powerful, but expensive.

The original fallback policy allowed the expensive path too often.

Now:

- fast HTTP is always tried first
- PDFs stay on the fast path
- browser fallback is only allowed for higher-value sources

Caller side:

```python
allow_browser_fallback = (
    bool(sample.get("is_primary_source", False))
    or int(sample.get("source_trust_score", 0)) >= SCRAPE_BROWSER_TRUST_THRESHOLD
    or int(sample.get("deterministic_score", 0)) >= SCRAPE_BROWSER_SCORE_THRESHOLD
)
```

Tool side:

```python
def scrape_site_detailed(url: str, allow_browser_fallback: bool = True, min_body_length: int = 400) -> dict[str, Any]:
    ...
    if not allow_browser_fallback or _looks_like_pdf(url):
        return {
            "scrape_method": "fast_http_thin",
            ...
        }
```

Why this helped:

- browser work became a deliberate escalation path
- weak sources fail fast
- strong sources still get the extra effort

General lesson:

Not all retrieval failures deserve the same retry or fallback policy.

Allocate effort by expected source value.

## 4.9 Better Runtime Observability

Files:

- `src/pipeline/main.py`
- `src/utils/utils.py`

We added richer runtime reporting so performance work could be measured instead of guessed.

Examples of metrics now exposed:

- `prepared_candidates`
- `judged_candidates`
- `accepted_candidates`
- `scrape_budget_per_pillar`
- `planned_scrape_tasks`
- `completed_scrape_tasks`
- `browser_fallback_allowed_count`
- `fast_only_count`
- `search_max_workers`
- `primary_source_counts_by_pillar`

This made it possible to answer questions like:

- Did we really reduce LLM judging volume?
- Did we actually cut scrape volume?
- Are we still overusing browser fallback?
- Are primary sources being discovered consistently?

General lesson:

Performance changes should produce new counters, not only new feelings.

## 5. What The New Runtime Proved

One of the clearest post-optimization runs was:

- `artifacts/LLY/analysis/runtime_profile.json`

Key observations:

- total runtime: about `15.9 min`
- `02b_primary_source_discovery`: active and working
- search: about `13.8s`
- filter: about `78.1s`
- scrape: about `688.4s`
- filter judged only `144` candidates out of `629`
- scrape budget reduced work to `48` documents

That run showed that the architecture changes were real, not cosmetic.

It also surfaced the next bottleneck:

- scraping still dominated wall-clock time
- some downstream evidence extraction quality issues remained

This is normal. Good optimization work reveals the next actual problem.

## 6. Reusable Playbook

If you are building a similar system, this sequence is a strong default.

### Step 1: Profile first

Before optimizing:

- measure every stage
- record counts, durations, and outputs

Without that, you will optimize the wrong layer.

### Step 2: Reduce input size before model reasoning

Add a deterministic pre-filter:

- entity match
- source quality
- keyword relevance
- freshness
- dedupe

Then send only the best subset to the model.

### Step 3: Batch repetitive model decisions

If the model is being asked the same question many times:

- batch small groups
- parallelize batches with bounded workers

### Step 4: Introduce explicit budgets

Budget expensive steps like:

- documents scraped per pillar
- candidates judged per pillar
- browser fallbacks allowed

Budgets make the system controllable.

### Step 5: Parallelize independent I/O

Parallelize:

- search
- scraping
- discovery

But keep:

- bounded worker pools
- per-domain throttles
- deterministic ordering where needed

### Step 6: Separate source discovery from broad search

For domains with canonical source types:

- create dedicated discovery for them
- merge them into general retrieval later

This improves both:

- quality
- efficiency

### Step 7: Use tiered effort policies

High-value sources:

- longer timeouts
- retries
- richer fallbacks

Low-value sources:

- fast path only
- fail quickly

### Step 8: Expand observability whenever you optimize

Every performance redesign should add counters that show whether it actually took effect.

## 7. Tradeoffs And Cautions

These optimizations come with tradeoffs.

### Deterministic filtering can miss outliers

If your cheap scorer is too aggressive, you can drop useful sources before the model sees them.

Mitigation:

- use generous enough thresholds
- prefer ranking + capping over hard rejection when possible

### Parallelism can create external pressure

Higher concurrency can:

- trigger provider throttling
- overload a domain
- create noisy failures

Mitigation:

- use bounded workers
- add per-domain caps
- instrument error rates

### Batching can reduce per-item nuance

Model batches are faster, but each item gets less individualized prompt space.

Mitigation:

- keep batches small
- use structured output
- combine with strong pre-filtering

### Source prioritization can bias the corpus

If primary sources dominate too heavily, you may underweight independent critique.

Mitigation:

- keep some room for high-quality secondary sources
- treat source diversity as a design goal, not an accident

## 8. Where To Look In This Codebase

Core files:

- `src/pipeline/main.py`
- `src/utils/utils.py`
- `src/tools/main.py`
- `src/prompts/tool_prompts.py`
- `docs/performance-baseline.md`

Useful runtime artifacts:

- `artifacts/<TICKER>/analysis/runtime_profile.json`
- `artifacts/<TICKER>/_pipeline_state/*.json`

## 9. Final Takeaway

The key shift was not "make it faster" in the abstract.

It was:

- do less expensive work
- do the remaining expensive work in parallel
- spend that work on better sources
- measure the effect clearly

That combination is what made the system materially faster while also improving the quality of retrieved evidence.
