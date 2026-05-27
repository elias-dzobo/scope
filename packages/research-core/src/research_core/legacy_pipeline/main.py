""" Stock Research Pipeline """
from __future__ import annotations

#external
import json
import os
from datetime import datetime, timezone
from time import perf_counter
from collections import defaultdict
from typing import Callable

#internal
from research_core.utils.utils import (
    execute_search_queries,
    prepare_stock_dir,
    create_research_artifacts,
    discover_primary_sources,
    execute_site_scrape,
    filter_results,
    get_data_size,
    get_last_filter_metadata,
    get_last_primary_discovery_metadata,
    get_last_scrape_metadata,
    merge_primary_sources_with_search_results,
)
from provider_integrations.tools.main import generate_search_queries
from research_core.scoring.main import extract_evidence_facts, assess_pillars, build_stock_scorecard
from research_core.utils.logger import get_logger
from research_core.storage import ticker_artifact_dir
from scope_api.observability.metrics import observe_pipeline_stage
from scope_api.observability.telemetry import observe_span

logger = get_logger(__name__)
ProgressCallback = Callable[[str, dict], None]
_ACTIVE_PROGRESS_CALLBACK: ProgressCallback | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summarize_query_plan(query_plan: dict) -> dict:
    return {
        "pillar_count": len(query_plan.get("pillars", [])),
        "query_counts_by_pillar": {
            pillar["pillar_name"]: len(pillar.get("queries", []))
            for pillar in query_plan.get("pillars", [])
        },
        "total_queries": sum(len(pillar.get("queries", [])) for pillar in query_plan.get("pillars", [])),
    }


def _summarize_primary_sources(primary_sources: dict[str, list[dict]]) -> dict:
    counts = {pillar: len(items) for pillar, items in primary_sources.items()}
    type_counts: dict[str, int] = defaultdict(int)
    for items in primary_sources.values():
        for item in items:
            type_counts[item.get("discovery_type", "unknown")] += 1
    return {
        "primary_source_counts_by_pillar": counts,
        "total_primary_sources": sum(counts.values()),
        "primary_source_counts_by_type": dict(type_counts),
        **get_last_primary_discovery_metadata(),
    }


def _summarize_search_results(search_results: dict[str, list[dict]]) -> dict:
    provider_counts: dict[str, int] = defaultdict(int)
    total_query_duration = 0.0
    max_query_duration = 0.0
    query_count = 0

    for batches in search_results.values():
        for batch in batches:
            provider_counts[batch.get("provider", "none")] += 1
            duration = float(batch.get("query_duration_seconds", 0.0))
            total_query_duration += duration
            max_query_duration = max(max_query_duration, duration)
            query_count += 1

    return {
        "query_batch_count": get_data_size(search_results),
        "candidate_doc_counts": _pillar_doc_counts(search_results),
        "total_candidate_docs": sum(_pillar_doc_counts(search_results).values()),
        "provider_counts": dict(provider_counts),
        "avg_query_duration_seconds": round(total_query_duration / query_count, 3) if query_count else 0.0,
        "max_query_duration_seconds": round(max_query_duration, 3),
        "search_max_workers": int(os.getenv("RESEARCH_SEARCH_MAX_WORKERS", "8")),
    }


def _summarize_collection(values: dict[str, list[dict]], label: str) -> dict:
    counts = {pillar: len(items) for pillar, items in values.items()}
    return {
        f"{label}_counts": counts,
        f"total_{label}": sum(counts.values()),
    }


def _summarize_scraped_results(values: dict[str, list[dict]]) -> dict:
    counts = {pillar: len(items) for pillar, items in values.items()}
    scrape_method_counts: dict[str, int] = defaultdict(int)
    document_type_counts: dict[str, int] = defaultdict(int)
    primary_source_count = 0
    total_duration = 0.0
    total_characters = 0
    item_count = 0

    for items in values.values():
        for item in items:
            scrape_method_counts[item.get("scrape_method", "unknown")] += 1
            document_type_counts[item.get("document_type", "unknown")] += 1
            primary_source_count += int(bool(item.get("is_primary_source", False)))
            total_duration += float(item.get("scrape_duration_seconds", 0.0))
            total_characters += int(item.get("character_count", 0) or 0)
            item_count += 1

    return {
        "scraped_doc_counts": counts,
        "total_scraped_docs": sum(counts.values()),
        "scrape_method_counts": dict(scrape_method_counts),
        "document_type_counts": dict(document_type_counts),
        "primary_source_count": primary_source_count,
        "avg_scrape_duration_seconds": round(total_duration / item_count, 3) if item_count else 0.0,
        "total_extracted_characters": total_characters,
    }


def _summarize_pillar_assessments(pillar_assessments: dict[str, dict]) -> dict:
    return {
        "pillar_scores": {
            pillar: assessment["score"]
            for pillar, assessment in pillar_assessments.items()
        },
        "pillar_categories": {
            pillar: assessment.get("category", "")
            for pillar, assessment in pillar_assessments.items()
        },
    }


def _summarize_scorecard(scorecard: dict) -> dict:
    return {
        "overall_score": scorecard.get("overall_score", 0),
        "confidence": scorecard.get("confidence", 0.0),
        "recommendation": scorecard.get("recommendation", ""),
    }


def _build_runtime_profile(
    stock_name: str,
    ticker: str,
    run_started_at: str,
    stage_records: list[dict],
) -> dict:
    total_duration = sum(record["duration_seconds"] for record in stage_records)
    slowest = sorted(stage_records, key=lambda item: item["duration_seconds"], reverse=True)[:3]
    step_share = [
        {
            "stage": record["stage"],
            "duration_seconds": record["duration_seconds"],
            "share_of_total": round((record["duration_seconds"] / total_duration) if total_duration else 0.0, 3),
        }
        for record in stage_records
    ]

    parallelism_candidates: list[str] = []
    if any(record["stage"] == "03_search" for record in slowest):
        parallelism_candidates.append("Parallelize search queries within each pillar with bounded concurrency.")
    if any(record["stage"] == "05_scrape" for record in slowest):
        parallelism_candidates.append("Parallelize page scraping with a small worker pool and per-domain limits.")
    if any(record["stage"] == "07_extract" for record in slowest):
        parallelism_candidates.append("Batch or parallelize evidence extraction per pillar/document chunk.")

    return {
        "stock_name": stock_name,
        "ticker": ticker,
        "started_at": run_started_at,
        "completed_at": _utc_now_iso(),
        "total_duration_seconds": round(total_duration, 3),
        "steps": stage_records,
        "slowest_steps": slowest,
        "step_share": step_share,
        "parallelism_candidates": parallelism_candidates,
    }


def _run_stage(
    stage: str,
    runner: Callable[[], object],
    input_summary: dict,
    stage_records: list[dict],
) -> object:
    started_at = perf_counter()
    started_wall = _utc_now_iso()
    result = runner()
    duration = perf_counter() - started_at
    record = {
        "stage": stage,
        "started_at": started_wall,
        "completed_at": _utc_now_iso(),
        "duration_seconds": round(duration, 3),
        "input_summary": input_summary,
        "output_summary": {},
        "status": "completed",
    }
    stage_records.append(record)
    return result


def _write_stage_checkpoint(ticker: str, stage: str, payload: dict) -> None:
    state_dir = ticker_artifact_dir(ticker) / "_pipeline_state"
    prepare_stock_dir(ticker)
    os.makedirs(state_dir, exist_ok=True)
    with open(state_dir / f"{stage}.json", "w") as f:
        json.dump(payload, f, indent=2)


def _pillar_doc_counts(search_results: dict[str, list[dict]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for pillar, query_batches in search_results.items():
        for batch in query_batches:
            counts[pillar] += len(batch.get("results", []))
    return dict(counts)


def _stage_prepare_artifacts(ticker: str) -> None:
    logger.info("Preparing artifact directory for ticker=%s", ticker)
    with observe_pipeline_stage("01_prepare"):
        with observe_span("pipeline.prepare_artifacts", {"pipeline.stage": "01_prepare", "ticker": ticker}):
            prepare_stock_dir(ticker)
    _write_stage_checkpoint(ticker, "01_prepare", {"ticker": ticker, "status": "completed"})


def _stage_generate_queries(stock_name: str, ticker: str, selected_pillars: list[str] | None = None) -> dict:
    logger.info("Generating search queries for stock=%s", stock_name)
    with observe_span(
        "pipeline.generate_queries",
        {"pipeline.stage": "02_query_plan", "stock_name": stock_name, "ticker": ticker},
    ):
        query_plan = generate_search_queries(stock_name)
    if selected_pillars:
        selected = set(selected_pillars)
        query_plan["pillars"] = [
            pillar for pillar in query_plan["pillars"] if pillar["pillar_name"] in selected
        ]
    _write_stage_checkpoint(
        ticker,
        "02_query_plan",
        {
            "stock": query_plan.get("stock", stock_name),
            "pillars": [
                {
                    "pillar_name": pillar["pillar_name"],
                    "query_count": len(pillar["queries"]),
                }
                for pillar in query_plan["pillars"]
            ],
        },
    )
    return query_plan


def _stage_execute_search(query_plan: dict, ticker: str) -> dict:
    logger.info("Executing generated search queries")
    with observe_span("pipeline.execute_search", {"pipeline.stage": "03_search", "ticker": ticker}):
        search_results = execute_search_queries(query_plan, progress_callback=_ACTIVE_PROGRESS_CALLBACK)
    _write_stage_checkpoint(
        ticker,
        "03_search",
        {
            "query_batch_counts": {pillar: len(batches) for pillar, batches in search_results.items()},
            "candidate_doc_counts": _pillar_doc_counts(search_results),
            "total_query_batches": get_data_size(search_results),
            "search_summary": _summarize_search_results(search_results),
        },
    )
    return search_results


def _stage_discover_primary_sources(stock_name: str, ticker: str) -> dict:
    logger.info("Discovering primary sources for stock=%s", stock_name)
    with observe_span("pipeline.discover_primary_sources", {"pipeline.stage": "02b_primary_source_discovery", "ticker": ticker}):
        primary_sources = discover_primary_sources(stock_name, ticker=ticker, progress_callback=_ACTIVE_PROGRESS_CALLBACK)
    _write_stage_checkpoint(
        ticker,
        "02b_primary_source_discovery",
        _summarize_primary_sources(primary_sources),
    )
    return primary_sources


def _stage_filter_results(stock_name: str, search_results: dict, ticker: str) -> dict:
    logger.info("Filtering scraped content for relevance")
    with observe_span("pipeline.filter_results", {"pipeline.stage": "04_filter", "ticker": ticker}):
        filtered_results = filter_results(stock_name, search_results, ticker=ticker, progress_callback=_ACTIVE_PROGRESS_CALLBACK)
    _write_stage_checkpoint(
        ticker,
        "04_filter",
        {
            "filtered_counts": {pillar: len(values) for pillar, values in filtered_results.items()},
            "total_filtered_docs": get_data_size(filtered_results),
            "filter_metadata": get_last_filter_metadata(),
        },
    )
    return filtered_results


def _stage_scrape(filtered_results: dict, ticker: str) -> dict:
    logger.info("Scraping site content from search results")
    with observe_span("pipeline.scrape", {"pipeline.stage": "05_scrape", "ticker": ticker}):
        scraped_results = execute_site_scrape(filtered_results, progress_callback=_ACTIVE_PROGRESS_CALLBACK)
    _write_stage_checkpoint(
        ticker,
        "05_scrape",
        {
            "scraped_counts": {pillar: len(values) for pillar, values in scraped_results.items()},
            "total_scraped_docs": get_data_size(scraped_results),
            "scrape_summary": _summarize_scraped_results(scraped_results),
            "scrape_metadata": get_last_scrape_metadata(),
        },
    )
    return scraped_results


def _stage_persist_artifacts(scrape_results: dict, ticker: str) -> None:
    logger.info("Writing filtered artifacts for ticker=%s", ticker)
    with observe_span("pipeline.persist_artifacts", {"pipeline.stage": "06_persist", "ticker": ticker}):
        create_research_artifacts(scrape_results, ticker)
    _write_stage_checkpoint(
        ticker,
        "06_persist",
        {"status": "completed", "artifact_count": len(scrape_results)},
    )


def _write_analysis_artifact(ticker: str, filename: str, payload: dict) -> None:
    analysis_dir = ticker_artifact_dir(ticker) / "analysis"
    os.makedirs(analysis_dir, exist_ok=True)
    with open(analysis_dir / filename, "w") as f:
        json.dump(payload, f, indent=2)


def _stage_extract_evidence(scrape_results: dict, stock_name: str, ticker: str) -> dict:
    logger.info("Extracting structured evidence facts")
    with observe_span("pipeline.extract_evidence", {"pipeline.stage": "07_extract", "ticker": ticker}):
        evidence_by_pillar = extract_evidence_facts(
            scrape_results,
            stock_name=stock_name,
            ticker=ticker,
            progress_callback=_ACTIVE_PROGRESS_CALLBACK,
        )
    _write_analysis_artifact(ticker, "evidence.json", evidence_by_pillar)
    _write_stage_checkpoint(
        ticker,
        "07_extract",
        {
            "evidence_counts": {pillar: len(facts) for pillar, facts in evidence_by_pillar.items()},
        },
    )
    return evidence_by_pillar


def _stage_assess_pillars(evidence_by_pillar: dict, ticker: str) -> dict:
    logger.info("Assessing pillars from extracted evidence")
    with observe_span("pipeline.assess_pillars", {"pipeline.stage": "08_assess", "ticker": ticker}):
        pillar_assessments = assess_pillars(evidence_by_pillar)
    _write_analysis_artifact(ticker, "pillar_assessments.json", pillar_assessments)
    _write_stage_checkpoint(
        ticker,
        "08_assess",
        {
            "pillar_scores": {
                pillar: assessment["score"]
                for pillar, assessment in pillar_assessments.items()
            },
        },
    )
    return pillar_assessments


def _stage_build_scorecard(
    stock_name: str,
    ticker: str,
    pillar_assessments: dict,
    evidence_by_pillar: dict[str, list[dict]],
) -> dict:
    logger.info("Building stock scorecard")
    with observe_span("pipeline.build_scorecard", {"pipeline.stage": "09_score", "ticker": ticker}):
        scorecard = build_stock_scorecard(stock_name, ticker, pillar_assessments, evidence_by_pillar)
    _write_analysis_artifact(ticker, "scorecard.json", scorecard)
    _write_stage_checkpoint(
        ticker,
        "09_score",
        {
            "overall_score": scorecard["overall_score"],
            "confidence": scorecard["confidence"],
            "recommendation": scorecard["recommendation"],
        },
    )
    return scorecard



def stock_research_pipeline(
    stock_name: str,
    ticker: str,
    selected_pillars: list[str] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict:
    global _ACTIVE_PROGRESS_CALLBACK
    _ACTIVE_PROGRESS_CALLBACK = progress_callback
    run_started_at = _utc_now_iso()
    stage_records: list[dict] = []

    def emit(stage: str, payload: dict) -> None:
        if progress_callback:
            progress_callback(stage, payload)

    emit("01_prepare", {"status": "running"})
    _run_stage(
        "01_prepare",
        lambda: _stage_prepare_artifacts(ticker),
        {"ticker": ticker},
        stage_records,
    )
    emit("01_prepare", {"status": "completed"})
    stage_records[-1]["output_summary"] = {"artifact_directory": str(ticker_artifact_dir(ticker))}

    emit("02_query_plan", {"status": "running"})
    with observe_pipeline_stage("02_query_plan"):
        query_plan = _run_stage(
            "02_query_plan",
            lambda: _stage_generate_queries(stock_name, ticker, selected_pillars=selected_pillars),
            {"stock_name": stock_name, "ticker": ticker},
            stage_records,
        )
    emit("02_query_plan", {"status": "completed", "pillar_count": len(query_plan["pillars"])})
    stage_records[-1]["output_summary"] = _summarize_query_plan(query_plan)

    emit("02b_primary_source_discovery", {"status": "running"})
    with observe_pipeline_stage("02b_primary_source_discovery"):
        primary_sources = _run_stage(
            "02b_primary_source_discovery",
            lambda: _stage_discover_primary_sources(stock_name, ticker),
            {"stock_name": stock_name, "ticker": ticker},
            stage_records,
        )
    emit("02b_primary_source_discovery", {"status": "completed", "primary_source_count": get_data_size(primary_sources)})
    stage_records[-1]["output_summary"] = _summarize_primary_sources(primary_sources)

    emit("03_search", {"status": "running"})
    with observe_pipeline_stage("03_search"):
        search_results = _run_stage(
            "03_search",
            lambda: _stage_execute_search(query_plan, ticker),
            _summarize_query_plan(query_plan),
            stage_records,
        )
    search_results = merge_primary_sources_with_search_results(search_results, primary_sources)
    emit("03_search", {"status": "completed", "query_batch_count": get_data_size(search_results)})
    stage_records[-1]["output_summary"] = _summarize_search_results(search_results)

    emit("04_filter", {"status": "running"})
    with observe_pipeline_stage("04_filter"):
        filtered_results = _run_stage(
            "04_filter",
            lambda: _stage_filter_results(stock_name, search_results, ticker),
            _summarize_search_results(search_results),
            stage_records,
        )
    emit("04_filter", {"status": "completed", "filtered_doc_count": get_data_size(filtered_results)})
    stage_records[-1]["output_summary"] = {
        **_summarize_collection(filtered_results, "filtered_docs"),
        **get_last_filter_metadata(),
    }

    emit("05_scrape", {"status": "running"})
    with observe_pipeline_stage("05_scrape"):
        scraped_results = _run_stage(
            "05_scrape",
            lambda: _stage_scrape(filtered_results, ticker),
            _summarize_collection(filtered_results, "filtered_docs"),
            stage_records,
        )
    emit("05_scrape", {"status": "completed", "scraped_doc_count": get_data_size(scraped_results)})
    stage_records[-1]["output_summary"] = {
        **_summarize_scraped_results(scraped_results),
        **get_last_scrape_metadata(),
    }

    emit("06_persist", {"status": "running"})
    with observe_pipeline_stage("06_persist"):
        _run_stage(
            "06_persist",
            lambda: _stage_persist_artifacts(scraped_results, ticker),
            _summarize_collection(scraped_results, "scraped_docs"),
            stage_records,
        )
    emit("06_persist", {"status": "completed"})
    stage_records[-1]["output_summary"] = {"artifact_count": len(scraped_results), "ticker": ticker}

    emit("07_extract", {"status": "running"})
    with observe_pipeline_stage("07_extract"):
        evidence_by_pillar = _run_stage(
            "07_extract",
            lambda: _stage_extract_evidence(scraped_results, stock_name, ticker),
            _summarize_collection(scraped_results, "scraped_docs"),
            stage_records,
        )
    emit("07_extract", {"status": "completed"})
    stage_records[-1]["output_summary"] = _summarize_collection(evidence_by_pillar, "evidence_facts")

    emit("08_assess", {"status": "running"})
    with observe_pipeline_stage("08_assess"):
        pillar_assessments = _run_stage(
            "08_assess",
            lambda: _stage_assess_pillars(evidence_by_pillar, ticker),
            _summarize_collection(evidence_by_pillar, "evidence_facts"),
            stage_records,
        )
    emit("08_assess", {"status": "completed"})
    stage_records[-1]["output_summary"] = _summarize_pillar_assessments(pillar_assessments)

    emit("09_score", {"status": "running"})
    with observe_pipeline_stage("09_score"):
        scorecard = _run_stage(
            "09_score",
            lambda: _stage_build_scorecard(stock_name, ticker, pillar_assessments, evidence_by_pillar),
            _summarize_pillar_assessments(pillar_assessments),
            stage_records,
        )
    emit("09_score", {"status": "completed"})
    stage_records[-1]["output_summary"] = _summarize_scorecard(scorecard)

    runtime_profile = _build_runtime_profile(
        stock_name=stock_name,
        ticker=ticker,
        run_started_at=run_started_at,
        stage_records=stage_records,
    )
    _write_analysis_artifact(ticker, "runtime_profile.json", runtime_profile)
    _ACTIVE_PROGRESS_CALLBACK = None

    return {
        "ticker": ticker,
        "stock_name": stock_name,
        "selected_pillars": list(scraped_results.keys()),
        "query_batch_count": get_data_size(search_results),
        "primary_source_count": get_data_size(primary_sources),
        "filtered_doc_count": get_data_size(filtered_results),
        "scraped_doc_count": get_data_size(scraped_results),
        "overall_score": scorecard["overall_score"],
        "recommendation": scorecard["recommendation"],
        "scorecard": scorecard,
        "pillar_assessments": pillar_assessments,
        "evidence_by_pillar": evidence_by_pillar,
        "sources_by_pillar": scraped_results,
        "runtime_profile": runtime_profile,
    }
