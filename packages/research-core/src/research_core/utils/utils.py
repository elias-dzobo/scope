""" Utility functions """

#external
import os
import json
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from typing import Any, Callable
from urllib.parse import urlparse

#internal
from provider_integrations.tools.main import search_tool, scrape_site_detailed, llm_as_a_batch_judge
from research_core.storage import ticker_artifact_dir
from research_core.utils.logger import get_logger
from scope_api.observability.telemetry import observe_span

logger = get_logger(__name__)
ProgressCallback = Callable[[str, dict[str, Any]], None]
FILTER_BATCH_SIZE = int(os.getenv("RESEARCH_FILTER_BATCH_SIZE", "6"))
FILTER_MAX_WORKERS = int(os.getenv("RESEARCH_FILTER_MAX_WORKERS", "6"))
FILTER_TOP_PER_QUERY = int(os.getenv("RESEARCH_FILTER_TOP_PER_QUERY", "3"))
FILTER_TOP_PER_PILLAR = int(os.getenv("RESEARCH_FILTER_TOP_PER_PILLAR", "24"))
SEARCH_MAX_WORKERS = int(os.getenv("RESEARCH_SEARCH_MAX_WORKERS", "8"))
PRIMARY_DISCOVERY_MAX_WORKERS = int(os.getenv("RESEARCH_PRIMARY_DISCOVERY_MAX_WORKERS", "6"))
PRIMARY_DISCOVERY_TOP_PER_TYPE = int(os.getenv("RESEARCH_PRIMARY_DISCOVERY_TOP_PER_TYPE", "3"))
SCRAPE_MAX_WORKERS = int(os.getenv("RESEARCH_SCRAPE_MAX_WORKERS", "8"))
SCRAPE_DOMAIN_CONCURRENCY = int(os.getenv("RESEARCH_SCRAPE_DOMAIN_CONCURRENCY", "2"))
SCRAPE_TOP_PER_PILLAR = int(os.getenv("RESEARCH_SCRAPE_TOP_PER_PILLAR", "8"))
SCRAPE_BROWSER_TRUST_THRESHOLD = int(os.getenv("RESEARCH_SCRAPE_BROWSER_TRUST_THRESHOLD", "70"))
SCRAPE_BROWSER_SCORE_THRESHOLD = int(os.getenv("RESEARCH_SCRAPE_BROWSER_SCORE_THRESHOLD", "60"))
SCRAPE_FAST_MIN_BODY_CHARS = int(os.getenv("RESEARCH_SCRAPE_FAST_MIN_BODY_CHARS", "400"))
SCRAPE_PRIMARY_FAST_MIN_BODY_CHARS = int(os.getenv("RESEARCH_SCRAPE_PRIMARY_FAST_MIN_BODY_CHARS", "250"))

_LAST_FILTER_METADATA: dict[str, Any] = {}
_LAST_SCRAPE_METADATA: dict[str, Any] = {}
_LAST_PRIMARY_DISCOVERY_METADATA: dict[str, Any] = {}


def _build_entity_terms(stock_name: str, ticker: str = "") -> set[str]:
    terms: set[str] = set()
    if ticker.strip():
        terms.add(ticker.strip().lower())

    lowered = stock_name.lower()
    cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
    for token in cleaned.split():
        if len(token) >= 3 and token not in {"plc", "ltd", "bank", "group", "company", "corporation"}:
            terms.add(token)
    if lowered.strip():
        terms.add(lowered.strip())
    return terms


def _mentions_entity(text: str, entity_terms: set[str]) -> bool:
    if not entity_terms:
        return True
    lowered = text.lower()
    return any(term in lowered for term in entity_terms)


def _classify_candidate_source(candidate: dict[str, Any]) -> dict[str, Any]:
    link = str(candidate.get("link", ""))
    title = str(candidate.get("title", ""))
    snippet = str(candidate.get("snippet", ""))
    combined = f"{title}\n{snippet}\n{link}".lower()
    host = (urlparse(link).netloc or "").lower()

    is_pdf = link.lower().endswith(".pdf") or "filetype:pdf" in combined or "/document/" in link.lower()
    document_type = "pdf" if is_pdf else "html"

    if "sec.gov" in host or "sedar" in host or "stockexchange" in host:
        source_kind = "regulatory"
    elif any(
        token in combined
        for token in (
            "investor relations",
            "investor center",
            "investor presentation",
            "annual report",
            "quarterly report",
            "earnings release",
            "results presentation",
            "interim report",
        )
    ) or any(token in link.lower() for token in ("/investor", "/investors", "/shareholders", "/annual-report")):
        source_kind = "investor_relations"
    elif any(token in host for token in ("annualreports.", "africanfinancials.", "marketscreener.", "morningstar.", "wsj.", "ft.com")):
        source_kind = "financial_data"
    elif "seekingalpha.com" in host or "fool.com" in host or "benzinga.com" in host:
        source_kind = "financial_publisher"
    else:
        source_kind = "general_web"

    is_primary_source = source_kind in {"regulatory", "investor_relations"} or (
        source_kind == "financial_data" and is_pdf
    )
    return {
        "source_kind": source_kind,
        "document_type": document_type,
        "is_primary_source": is_primary_source,
        "host": host,
    }


def _score_candidate(
    pillar: str,
    candidate: dict[str, Any],
    classification: dict[str, Any],
    entity_terms: set[str],
) -> int:
    title = str(candidate.get("title", ""))
    snippet = str(candidate.get("snippet", ""))
    link = str(candidate.get("link", ""))
    intent = str(candidate.get("intent", ""))
    text = f"{title}\n{snippet}\n{link}\n{intent}"
    lowered = text.lower()
    score = 0

    if _mentions_entity(text, entity_terms):
        score += 25
        exact_name_terms = [term for term in entity_terms if " " in term]
        if any(term in lowered for term in exact_name_terms):
            score += 15
    else:
        score -= 40

    if classification["is_primary_source"]:
        score += 30
    elif classification["source_kind"] == "financial_data":
        score += 18
    elif classification["source_kind"] == "financial_publisher":
        score += 10

    if classification["document_type"] == "pdf":
        score += 8

    pillar_keywords = {
        "Macro & Industry": ["industry", "market", "demand", "capacity", "power prices", "commodity", "regulation"],
        "Economic Moat": ["competitive", "moat", "advantage", "market share", "contract", "customer"],
        "Financial Engine": ["revenue", "eps", "margin", "cash flow", "ebitda", "guidance"],
        "Management & Capital Allocation": ["management", "board", "governance", "capital allocation", "buyback", "dividend"],
        "Valuation": ["valuation", "price target", "fair value", "p/e", "ev/ebitda", "multiple"],
        "Technical Analysis": ["price", "volume", "moving average", "rsi", "breakout", "momentum"],
    }
    score += sum(4 for keyword in pillar_keywords.get(pillar, []) if keyword in lowered)

    if re.search(r"\b20(2[3-9]|3[0-9])\b", lowered):
        score += 4
    if any(token in lowered for token in ["q1", "q2", "q3", "q4", "fy2025", "fy2026", "ttm"]):
        score += 4

    if any(term in lowered for term in ["press release", "opinion", "blog", "rumor"]):
        score -= 8

    return score


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
        {
            "discovery_type": "annual_report",
            "pillar_candidates": ["Financial Engine", "Management & Capital Allocation", "Valuation", "Economic Moat", "Macro & Industry"],
            "query": f"{quoted}{ticker_part} annual report filetype:pdf",
            "intent": "Find annual-report PDFs and long-form primary disclosures.",
        },
        {
            "discovery_type": "quarterly_results",
            "pillar_candidates": ["Financial Engine", "Valuation"],
            "query": f"{quoted}{ticker_part} quarterly results OR earnings release filetype:pdf",
            "intent": "Find recent quarterly results and earnings-release PDFs.",
        },
        {
            "discovery_type": "earnings_presentation",
            "pillar_candidates": ["Financial Engine", "Valuation", "Economic Moat"],
            "query": f"{quoted}{ticker_part} earnings presentation OR investor presentation filetype:pdf",
            "intent": "Find earnings or investor presentations.",
        },
        {
            "discovery_type": "proxy_governance",
            "pillar_candidates": ["Management & Capital Allocation"],
            "query": f"{quoted}{ticker_part} proxy statement OR corporate governance filetype:pdf",
            "intent": "Find governance, proxy, and shareholder materials.",
        },
        {
            "discovery_type": "regulatory_filing",
            "pillar_candidates": ["Financial Engine", "Management & Capital Allocation", "Valuation"],
            "query": f"{quoted}{ticker_part} 10-K OR 20-F OR annual report site:sec.gov",
            "intent": "Find regulatory filings and official long-form disclosures.",
        },
        {
            "discovery_type": "transcript",
            "pillar_candidates": ["Financial Engine", "Valuation", "Management & Capital Allocation"],
            "query": f"{quoted}{ticker_part} earnings call transcript",
            "intent": "Find management commentary from earnings call transcripts.",
        },
    ]


def discover_primary_sources(
    stock_name: str,
    ticker: str = "",
    progress_callback: ProgressCallback | None = None,
) -> dict[str, list[dict[str, Any]]]:
    logger.info("Discovering primary sources for stock=%s", stock_name)
    global _LAST_PRIMARY_DISCOVERY_METADATA
    discovery_queries = _build_primary_discovery_queries(stock_name, ticker)
    total_queries = len(discovery_queries)
    completed_queries = 0
    entity_terms = _build_entity_terms(stock_name, ticker)
    discovered_by_pillar: dict[str, list[dict[str, Any]]] = defaultdict(list)
    type_counts: dict[str, int] = defaultdict(int)

    def run_discovery(item: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        results = search_tool({"query": item["query"]})
        return {
            "query": item["query"],
            "intent": item["intent"],
            "discovery_type": item["discovery_type"],
            "pillar_candidates": item["pillar_candidates"],
            "results": results,
            "query_duration_seconds": round(time.perf_counter() - started, 3),
        }

    with ThreadPoolExecutor(max_workers=max(1, min(PRIMARY_DISCOVERY_MAX_WORKERS, total_queries or 1))) as executor:
        futures = [executor.submit(run_discovery, item) for item in discovery_queries]
        for future in as_completed(futures):
            payload = future.result()
            discovery_type = payload["discovery_type"]
            ranked: list[dict[str, Any]] = []
            for candidate in payload["results"]:
                classification = _classify_candidate_source(candidate)
                candidate_copy = dict(candidate)
                candidate_copy.update(classification)
                candidate_copy["query"] = payload["query"]
                candidate_copy["intent"] = payload["intent"]
                candidate_copy["discovery_type"] = discovery_type
                candidate_copy["source_origin"] = "primary_discovery"
                candidate_copy["deterministic_score"] = _score_candidate(
                    pillar=payload["pillar_candidates"][0],
                    candidate=candidate_copy,
                    classification=classification,
                    entity_terms=entity_terms,
                )
                if not classification["is_primary_source"] and classification["source_kind"] not in {"financial_data", "financial_publisher"}:
                    continue
                if candidate_copy["deterministic_score"] <= 0:
                    continue
                ranked.append(candidate_copy)
            ranked.sort(
                key=lambda item: (
                    int(bool(item.get("is_primary_source", False))),
                    int(item.get("deterministic_score", 0)),
                ),
                reverse=True,
            )
            selected = ranked[:PRIMARY_DISCOVERY_TOP_PER_TYPE]
            for pillar in payload["pillar_candidates"]:
                discovered_by_pillar[pillar].extend(selected)
            type_counts[discovery_type] += len(selected)
            completed_queries += 1
            if progress_callback:
                progress_callback(
                    "02b_primary_source_discovery",
                    {
                        "status": "running",
                        "current_substep": f"primary discovery query {completed_queries}/{total_queries}",
                        "stage_current": completed_queries,
                        "stage_total": total_queries,
                        "stage_progress": round((completed_queries / total_queries) * 100, 1) if total_queries else 0,
                        "discovery_type": discovery_type,
                    },
                )

    deduped_by_pillar: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pillar, items in discovered_by_pillar.items():
        seen_links: set[str] = set()
        for item in sorted(
            items,
            key=lambda candidate: (
                int(bool(candidate.get("is_primary_source", False))),
                int(candidate.get("deterministic_score", 0)),
            ),
            reverse=True,
        ):
            link = item.get("link", "")
            if not link or link in seen_links:
                continue
            deduped_by_pillar[pillar].append(item)
            seen_links.add(link)

    _LAST_PRIMARY_DISCOVERY_METADATA = {
        "discovery_query_count": total_queries,
        "primary_source_counts_by_pillar": {pillar: len(items) for pillar, items in deduped_by_pillar.items()},
        "primary_source_counts_by_type": dict(type_counts),
        "total_primary_sources": sum(len(items) for items in deduped_by_pillar.values()),
        "max_workers": PRIMARY_DISCOVERY_MAX_WORKERS,
        "top_per_type": PRIMARY_DISCOVERY_TOP_PER_TYPE,
    }
    return deduped_by_pillar


def merge_primary_sources_with_search_results(
    search_results: dict[str, list[dict[str, Any]]],
    primary_sources: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pillar, batches in search_results.items():
        merged[pillar].extend(batches)
    for pillar, items in primary_sources.items():
        if not items:
            continue
        merged[pillar].append(
            {
                "query": f"{pillar} primary-source discovery",
                "intent": "Primary-source discovery candidates",
                "results": items,
                "query_duration_seconds": 0.0,
                "result_count": len(items),
                "provider": "primary_discovery",
                "source_origin": "primary_discovery",
            }
        )
    return dict(merged)


def _prepare_filter_candidates(
    pillar: str,
    values: list[dict[str, Any]],
    entity_terms: set[str],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for batch in values:
        query_text = batch.get("query", "")
        intent = batch.get("intent", "")
        batch_ranked: list[dict[str, Any]] = []
        for candidate in batch.get("results", []):
            classification = _classify_candidate_source(candidate)
            candidate_copy = dict(candidate)
            candidate_copy["intent"] = intent
            candidate_copy["query"] = query_text
            candidate_copy.update(classification)
            candidate_copy["deterministic_score"] = _score_candidate(
                pillar=pillar,
                candidate=candidate_copy,
                classification=classification,
                entity_terms=entity_terms,
            )
            if candidate_copy["deterministic_score"] <= 0:
                continue
            batch_ranked.append(candidate_copy)
        batch_ranked.sort(key=lambda item: item["deterministic_score"], reverse=True)
        ranked.extend(batch_ranked[:FILTER_TOP_PER_QUERY])

    deduped: list[dict[str, Any]] = []
    seen_links: set[str] = set()
    seen_host_title: set[tuple[str, str]] = set()
    for candidate in sorted(ranked, key=lambda item: item["deterministic_score"], reverse=True):
        link = candidate.get("link", "")
        host_title = (candidate.get("host", ""), candidate.get("title", "").strip().lower())
        if not link or link in seen_links or host_title in seen_host_title:
            continue
        deduped.append(candidate)
        seen_links.add(link)
        seen_host_title.add(host_title)
        if len(deduped) >= FILTER_TOP_PER_PILLAR:
            break
    return deduped


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
    by_id = {item["candidate_id"]: item for item in batch_items}
    merged: list[dict[str, Any]] = []
    for evaluation in evaluations:
        candidate = by_id.get(evaluation.get("candidate_id", ""))
        if not candidate:
            continue
        merged_item = dict(candidate)
        merged_item["source_trust_score"] = int(evaluation.get("source_trust_score", 0))
        merged_item["judge_summary"] = evaluation.get("summary", "")
        merged_item["is_relevant"] = bool(evaluation.get("is_relevant", False))
        merged.append(merged_item)
    return merged


def prepare_stock_dir(ticker: str):
    logger.info("Creating artifact directory for ticker=%s", ticker)
    ticker_artifact_dir(ticker).mkdir(parents=True, exist_ok=True)


def execute_search_queries(
    search_queries: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> dict[str, list[dict[str, Any]]]:
    logger.info("Executing search queries across pillars")
    search_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total_queries = sum(len(pillar["queries"]) for pillar in search_queries["pillars"])
    completed_queries = 0
    tasks: list[tuple[str, int, str, str]] = []
    for pillar in search_queries["pillars"]:
        pillar_name = pillar["pillar_name"]
        queries = pillar["queries"]
        logger.info("Processing pillar=%s with %d queries", pillar_name, len(queries))
        for query_index, query_payload in enumerate(queries):
            query_text = query_payload["query"] if isinstance(query_payload, dict) else str(query_payload)
            intent = query_payload.get("intent", "") if isinstance(query_payload, dict) else ""
            tasks.append((pillar_name, query_index, query_text, intent))

    def run_query(task: tuple[str, int, str, str]) -> tuple[str, int, dict[str, Any]]:
        pillar_name, query_index, query_text, intent = task
        started = time.perf_counter()
        with observe_span(
            "utils.execute_search_query",
            {"pillars.pillar_name": pillar_name, "search.intent": intent},
        ):
            results = search_tool({"query": query_text})
        duration_seconds = time.perf_counter() - started
        return pillar_name, query_index, {
            "query": query_text,
            "intent": intent,
            "results": results,
            "query_duration_seconds": round(duration_seconds, 3),
            "result_count": len(results),
            "provider": results[0].get("provider", "none") if results else "none",
            "_query_index": query_index,
        }

    with ThreadPoolExecutor(max_workers=max(1, min(SEARCH_MAX_WORKERS, total_queries or 1))) as executor:
        futures = [executor.submit(run_query, task) for task in tasks]
        for future in as_completed(futures):
            pillar_name, _, result_item = future.result()
            search_results[pillar_name].append(result_item)
            completed_queries += 1
            if progress_callback:
                progress_callback(
                    "03_search",
                    {
                        "status": "running",
                        "current_substep": f"search query {completed_queries}/{total_queries}",
                        "stage_current": completed_queries,
                        "stage_total": total_queries,
                        "stage_progress": round((completed_queries / total_queries) * 100, 1) if total_queries else 0,
                        "provider": result_item.get("provider", "none"),
                        "query": result_item.get("query", ""),
                    },
                )

    for pillar_name, batches in search_results.items():
        batches.sort(key=lambda item: item.get("_query_index", 0))
        for item in batches:
            item.pop("_query_index", None)

    logger.info("Completed all search queries")
    return search_results


def execute_site_scrape(
    search_results: dict[str, list[dict[str, Any]]],
    progress_callback: ProgressCallback | None = None,
) -> dict[str, list[dict[str, str]]]:
    logger.info("Starting site scrape for search results")
    global _LAST_SCRAPE_METADATA
    results: dict[str, list[dict[str, str]]] = defaultdict(list)

    prioritized_by_pillar: dict[str, list[dict[str, Any]]] = {}
    for pillar, values in search_results.items():
        prioritized = sorted(
            values,
            key=lambda item: (
                int(bool(item.get("is_primary_source", False))),
                int(item.get("source_trust_score", 0)),
                int(item.get("deterministic_score", 0)),
            ),
            reverse=True,
        )[:SCRAPE_TOP_PER_PILLAR]
        prioritized_by_pillar[pillar] = prioritized

    tasks = [
        (pillar, sample)
        for pillar, values in prioritized_by_pillar.items()
        for sample in values
        if sample.get("link")
    ]
    total_items = len(tasks)
    completed_items = 0
    browser_fallback_allowed_count = 0
    fast_only_count = 0

    domain_semaphores: dict[str, threading.BoundedSemaphore] = {}
    semaphore_lock = threading.Lock()

    def scrape_one(pillar: str, sample: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        host = sample.get("host") or urlparse(sample.get("link", "")).netloc.lower()
        allow_browser_fallback = bool(sample.get("is_primary_source", False)) or int(sample.get("source_trust_score", 0)) >= SCRAPE_BROWSER_TRUST_THRESHOLD or int(sample.get("deterministic_score", 0)) >= SCRAPE_BROWSER_SCORE_THRESHOLD
        min_body_length = SCRAPE_PRIMARY_FAST_MIN_BODY_CHARS if sample.get("is_primary_source", False) else SCRAPE_FAST_MIN_BODY_CHARS
        with semaphore_lock:
            semaphore = domain_semaphores.setdefault(
                host,
                threading.BoundedSemaphore(max(1, SCRAPE_DOMAIN_CONCURRENCY)),
            )
        with semaphore:
            try:
                with observe_span(
                    "utils.scrape_site",
                    {"url": sample.get("link", "")[:128], "pillar": pillar},
                ):
                    scraped = scrape_site_detailed(
                        sample["link"],
                        allow_browser_fallback=allow_browser_fallback,
                        min_body_length=min_body_length,
                    )
            except Exception:
                logger.exception("Scrape failed for url=%s", sample.get("link"))
                scraped = {
                    "title": "",
                    "body": "",
                    "scrape_method": "failed",
                    "source_kind": sample.get("document_type", "html"),
                    "content_type": "",
                    "scrape_duration_seconds": 0.0,
                    "character_count": 0,
                    "browser_ms_used": 0.0,
                }

        return pillar, {
            "title": scraped.get("title") or sample.get("title", ""),
            "body": scraped.get("body", ""),
            "link": sample.get("link", ""),
            "snippet": sample.get("snippet", ""),
            "source_trust_score": sample.get("source_trust_score", 0),
            "judge_summary": sample.get("judge_summary", ""),
            "source_kind": sample.get("source_kind", "general_web"),
            "document_type": sample.get("document_type", scraped.get("source_kind", "html")),
            "is_primary_source": sample.get("is_primary_source", False),
            "scrape_method": scraped.get("scrape_method", "unknown"),
            "scrape_duration_seconds": scraped.get("scrape_duration_seconds", 0.0),
            "content_type": scraped.get("content_type", ""),
            "character_count": scraped.get("character_count", 0),
            "browser_ms_used": scraped.get("browser_ms_used", 0.0),
            "deterministic_score": sample.get("deterministic_score", 0),
            "browser_fallback_allowed": allow_browser_fallback,
        }

    for _, sample in tasks:
        if bool(sample.get("is_primary_source", False)) or int(sample.get("source_trust_score", 0)) >= SCRAPE_BROWSER_TRUST_THRESHOLD or int(sample.get("deterministic_score", 0)) >= SCRAPE_BROWSER_SCORE_THRESHOLD:
            browser_fallback_allowed_count += 1
        else:
            fast_only_count += 1

    with ThreadPoolExecutor(max_workers=max(1, SCRAPE_MAX_WORKERS)) as executor:
        futures = [executor.submit(scrape_one, pillar, sample) for pillar, sample in tasks]
        for future in as_completed(futures):
            pillar, result_item = future.result()
            results[pillar].append(result_item)
            completed_items += 1
            if progress_callback:
                progress_callback(
                    "05_scrape",
                    {
                        "status": "running",
                        "current_substep": f"scraping document {completed_items}/{total_items}",
                        "stage_current": completed_items,
                        "stage_total": total_items,
                        "stage_progress": round((completed_items / total_items) * 100, 1) if total_items else 0,
                        "scrape_method": result_item.get("scrape_method", "unknown"),
                        "document_type": result_item.get("document_type", "html"),
                    },
                )

    for pillar in results:
        results[pillar].sort(
            key=lambda item: (
                int(bool(item.get("is_primary_source", False))),
                int(item.get("source_trust_score", 0)),
                int(item.get("deterministic_score", 0)),
            ),
            reverse=True,
        )

    logger.info("Completed scraping site content")
    _LAST_SCRAPE_METADATA = {
        "scrape_budget_per_pillar": SCRAPE_TOP_PER_PILLAR,
        "prioritized_candidate_counts": {pillar: len(items) for pillar, items in prioritized_by_pillar.items()},
        "planned_scrape_tasks": total_items,
        "completed_scrape_tasks": completed_items,
        "browser_fallback_allowed_count": browser_fallback_allowed_count,
        "fast_only_count": fast_only_count,
        "max_workers": SCRAPE_MAX_WORKERS,
        "per_domain_concurrency": SCRAPE_DOMAIN_CONCURRENCY,
    }
    return results


def create_research_artifacts(results: dict, ticker: str):
    logger.info("Persisting research artifacts for ticker=%s", ticker)
    output_dir = ticker_artifact_dir(ticker)
    output_dir.mkdir(parents=True, exist_ok=True)
    for key, result in results.items():
        with open(output_dir / f"{key}.json", "w") as f:
            json.dump(results[key], f)

    logger.info("Artifact persistence completed for ticker=%s", ticker)
    return


def filter_results(
    stock_name: str,
    results: dict[str, list[dict[str, Any]]],
    ticker: str = "",
    progress_callback: ProgressCallback | None = None,
) -> dict[str, list[dict[str, Any]]]:
    logger.info("Filtering results for stock=%s", stock_name)
    global _LAST_FILTER_METADATA
    filtered_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    entity_terms = _build_entity_terms(stock_name, ticker)
    total_candidates = sum(len(batch.get("results", [])) for values in results.values() for batch in values)
    prepared_candidates = 0
    judged_candidates = 0
    prepared_by_pillar: dict[str, int] = {}
    judged_by_pillar: dict[str, int] = {}
    accepted_by_pillar: dict[str, int] = {}

    for pillar, values in results.items():
        logger.info("Evaluating relevance for pillar=%s, query_batches=%d", pillar, len(values))
        selected_candidates = _prepare_filter_candidates(pillar, values, entity_terms)
        for index, candidate in enumerate(selected_candidates, start=1):
            candidate["candidate_id"] = f"{pillar[:2]}-{index}"
        prepared_candidates += len(selected_candidates)
        prepared_by_pillar[pillar] = len(selected_candidates)

        judged: list[dict[str, Any]] = []
        batches = [
            selected_candidates[i : i + FILTER_BATCH_SIZE]
            for i in range(0, len(selected_candidates), FILTER_BATCH_SIZE)
        ]
        if batches:
            with ThreadPoolExecutor(max_workers=max(1, FILTER_MAX_WORKERS)) as executor:
                futures = [
                    executor.submit(_judge_candidate_batch, pillar, stock_name, batch_items)
                    for batch_items in batches
                ]
                for future in as_completed(futures):
                    batch_result = future.result()
                    judged.extend(batch_result)
                    judged_candidates += len(batch_result)
                    judged_by_pillar[pillar] = judged_by_pillar.get(pillar, 0) + len(batch_result)
                    if progress_callback:
                        progress_callback(
                            "04_filter",
                            {
                                "status": "running",
                                "current_substep": f"judging result {judged_candidates}/{prepared_candidates}",
                                "stage_current": judged_candidates,
                                "stage_total": max(prepared_candidates, 1),
                                "stage_progress": round((judged_candidates / max(prepared_candidates, 1)) * 100, 1),
                                "pillar": pillar,
                            },
                        )

        seen_links: set[str] = set()
        for candidate in judged:
            link = candidate.get("link", "")
            minimum_trust = 35 if candidate.get("is_primary_source", False) else 45
            if candidate.get("is_relevant") and int(candidate.get("source_trust_score", 0)) >= minimum_trust and link and link not in seen_links:
                filtered_results[pillar].append(candidate)
                seen_links.add(link)
        filtered_results[pillar].sort(
            key=lambda item: (
                int(bool(item.get("is_primary_source", False))),
                int(item.get("deterministic_score", 0)),
                int(item.get("source_trust_score", 0)),
            ),
            reverse=True,
        )
        filtered_results[pillar] = filtered_results[pillar][:15]
        accepted_by_pillar[pillar] = len(filtered_results[pillar])

    logger.info("Filtering completed")
    _LAST_FILTER_METADATA = {
        "total_candidates": total_candidates,
        "prepared_candidates": prepared_candidates,
        "judged_candidates": judged_candidates,
        "accepted_candidates": sum(accepted_by_pillar.values()),
        "prepared_by_pillar": prepared_by_pillar,
        "judged_by_pillar": judged_by_pillar,
        "accepted_by_pillar": accepted_by_pillar,
        "filter_top_per_query": FILTER_TOP_PER_QUERY,
        "filter_top_per_pillar": FILTER_TOP_PER_PILLAR,
        "batch_size": FILTER_BATCH_SIZE,
        "max_workers": FILTER_MAX_WORKERS,
    }
    return filtered_results


def get_data_size(results: dict[str, list[dict[str, Any]]]) -> int:
    logger.info("Getting data size for results")
    data_size = 0
    for values in results.values():
        data_size += len(values)
    logger.info("Data size completed")
    return data_size


def get_last_filter_metadata() -> dict[str, Any]:
    return dict(_LAST_FILTER_METADATA)


def get_last_scrape_metadata() -> dict[str, Any]:
    return dict(_LAST_SCRAPE_METADATA)


def get_last_primary_discovery_metadata() -> dict[str, Any]:
    return dict(_LAST_PRIMARY_DISCOVERY_METADATA)
