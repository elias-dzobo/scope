# Performance Baseline

This document captures the current end-to-end runtime profile of the stock research pipeline before optimization work begins.

## Baseline Run

- Stock: `Vistra Corp`
- Ticker: `VST`
- Source artifact: `artifacts/VST/analysis/runtime_profile.json`
- Run started: `2026-04-02T12:25:25.514544+00:00`
- Run completed: `2026-04-02T13:22:48.278030+00:00`
- Total duration: `3442.414s` (`57.4 min`)

## Stage Breakdown

| Stage | Duration (s) | Share of total | Notes |
|---|---:|---:|---|
| `01_prepare` | 0.049 | 0.0% | Artifact directory setup |
| `02_query_plan` | 113.652 | 3.3% | LLM query generation and refinement |
| `03_search` | 84.633 | 2.5% | 60 search queries, 480 candidates returned |
| `04_filter` | 1603.663 | 46.6% | Relevance filtering from 480 candidates to 90 documents |
| `05_scrape` | 1456.410 | 42.3% | Scraping 90 filtered documents |
| `06_persist` | 0.118 | 0.0% | Writing research artifacts |
| `07_extract` | 183.879 | 5.3% | Evidence extraction from 90 scraped documents |
| `08_assess` | 0.006 | 0.0% | Pillar scoring |
| `09_score` | 0.004 | 0.0% | Final scorecard |

## Throughput Summary

### Query planning

- 6 pillars
- 10 queries per pillar
- 60 total queries
- Runtime: `113.652s`

### Search

- 60 query batches
- 480 total candidate documents
- 80 candidates per pillar
- Runtime: `84.633s`

### Filter

- Input: 480 candidate documents
- Output: 90 filtered documents
- Runtime: `1603.663s`
- Effective cost: about `3.34s` per candidate reviewed

### Scrape

- Input: 90 filtered documents
- Output: 90 scraped documents
- Runtime: `1456.410s`
- Effective cost: about `16.18s` per document scraped

### Extract

- Input: 90 scraped documents
- Output: 72 evidence facts
- Runtime: `183.879s`

## Current Bottlenecks

The run is dominated by two stages:

- `04_filter`: `46.6%`
- `05_scrape`: `42.3%`

Combined:

- `88.9%` of total runtime

This means optimization work should focus first on:

1. reducing filtering cost before LLM judgment
2. reducing scraping cost via better source selection and concurrency

## Current Architecture Observations

### Query planning

- Implemented in `src/tools/main.py`
- `generate_search_queries(...)`
- Uses LLM generation plus refinement pass

### Search

- Implemented in `src/utils/utils.py`
- `execute_search_queries(...)`
- Executes per-query search sequentially

### Filter

- Implemented in `src/utils/utils.py`
- `filter_results(...)`
- Calls `llm_as_a_judge(...)` from `src/tools/main.py`
- Currently judges candidates one-by-one

### Scrape

- Implemented in `src/utils/utils.py`
- `execute_site_scrape(...)`
- Uses `scrape_site_detailed(...)` from `src/tools/main.py`
- Currently scrapes filtered documents sequentially

### Extract

- Implemented in `src/agent/main.py`
- `extract_evidence_facts(...)`
- Runtime is material but not primary bottleneck

## Optimization Priority

### Priority 1

- `04_filter`
- deterministic pre-filtering
- candidate ranking before LLM judging
- batched LLM judging
- bounded concurrency

### Priority 2

- `05_scrape`
- parallel scraping
- per-domain concurrency limits
- source budget per pillar
- stronger primary-source preference

### Priority 3

- `02_query_plan`
- reduce prompt/output size
- optionally reduce query count or query-planning passes

### Priority 4

- `07_extract`
- parallelize by pillar or chunk if needed after filter/scrape improvements

## Success Criteria For Next Pass

Reasonable first optimization targets for a run shaped like `VST`:

- Cut `04_filter` by at least `50%`
- Cut `05_scrape` by at least `50%`
- Bring total runtime below `20 min`

Stretch target:

- Bring total runtime below `10–12 min` with batching, concurrency, and source-budgeting combined
