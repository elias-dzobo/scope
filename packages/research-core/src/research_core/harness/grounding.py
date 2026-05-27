"""Gemini grounding helpers for the research harness.

The functions in this module keep Gemini-specific response parsing out of the
controller. They return stable Pydantic models so the rest of the harness can
be tested without live network calls.
"""

from __future__ import annotations

import os
import re
from typing import Any

import requests

from research_core.scoring.main import PILLAR_SIGNALS
from research_core.harness.models import (
    GroundedCitationSupport,
    GroundedResearchResult,
    GroundedSource,
    ResearchBrief,
    ResearchWorkstream,
)


GEMINI_GROUNDED_MODEL = os.getenv("GEMINI_GROUNDED_MODEL", "gemini-2.5-flash")


def build_grounded_prompt(brief: ResearchBrief, workstream: ResearchWorkstream) -> str:
    """Build a focused grounded-research prompt for one workstream.

    When the workstream carries ``query_hints`` (set by QueryRefinementTool on
    a retry iteration), they are appended as priority search angles so Gemini
    targets the exact data the previous iteration missed.
    """
    entity = brief.entities[0] if brief.entities else None
    company = entity.name if entity else "the target company"
    ticker_hint = f" ({entity.ticker})" if entity and entity.ticker else ""
    requirements = "\n".join(
        f"- {item.name}: {item.description}" for item in workstream.required_evidence
    )
    focus = ", ".join(workstream.search_focus) or "authoritative and recent sources"
    base = (
        f"Research {company}{ticker_hint} for the {workstream.title} workstream.\n\n"
        f"Goal: {workstream.goal}\n\n"
        f"Required evidence:\n{requirements}\n\n"
        f"Prioritize: {focus}.\n"
        "Use primary sources, filings, investor relations pages, earnings materials, "
        "and reputable financial data where possible. Return concise, source-grounded "
        "evidence with concrete metrics and time periods. Do not make unsupported claims."
    )
    if workstream.query_hints:
        hints_text = "\n".join(f"- {h}" for h in workstream.query_hints)
        base += f"\n\nPriority search angles for this iteration:\n{hints_text}"
    return base


def call_gemini_grounded(prompt: str) -> dict[str, Any]:
    """Call Gemini with Google Search grounding via REST.

    The project already uses `requests`, so this avoids introducing a new SDK
    dependency. Missing credentials are represented as an unavailable response
    instead of raising, allowing local tests and fallback paths to continue.
    """
    api_key = os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {"status": "unavailable", "error_message": "GOOGLE_API_KEY/GEMINI_API_KEY is not configured"}

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_GROUNDED_MODEL}:generateContent"
    # thinkingBudget=0 disables the reasoning phase on gemini-2.5-flash,
    # cutting per-call latency from ~45s to ~10-15s without changing output quality
    # for factual grounded search tasks.  Safe to ignore on models that don't
    # support it — the API treats unknown generationConfig keys as no-ops.
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.1,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    response = requests.post(
        endpoint,
        params={"key": api_key},
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    data["status"] = "success"
    return data


def parse_grounded_response(
    response_payload: dict[str, Any],
    workstream: ResearchWorkstream,
    brief: ResearchBrief | None = None,
) -> GroundedResearchResult:
    """Convert a raw Gemini response into harness-native grounded research."""
    if response_payload.get("status") == "unavailable":
        return GroundedResearchResult(
            workstream_id=workstream.id,
            pillar_name=workstream.pillar_name,
            status="unavailable",
            error_message=response_payload.get("error_message", ""),
        )

    try:
        candidate = (response_payload.get("candidates") or [{}])[0]
        answer = _extract_answer_text(candidate)
        metadata = candidate.get("groundingMetadata") or candidate.get("grounding_metadata") or {}
        sources = _parse_grounded_sources(metadata)
        supports = _parse_grounding_supports(metadata, sources)
        evidence = _evidence_from_supports(workstream, supports, sources, brief=brief)
        return GroundedResearchResult(
            workstream_id=workstream.id,
            pillar_name=workstream.pillar_name,
            query=workstream.goal,
            answer=answer,
            web_search_queries=list(metadata.get("webSearchQueries") or metadata.get("web_search_queries") or []),
            sources=sources,
            citation_supports=supports,
            evidence_candidates=evidence,
            status="success",
        )
    except Exception as exc:  # pragma: no cover - defensive parse guard
        return GroundedResearchResult(
            workstream_id=workstream.id,
            pillar_name=workstream.pillar_name,
            status="failed",
            error_message=str(exc),
        )


def _extract_answer_text(candidate: dict[str, Any]) -> str:
    content = candidate.get("content") or {}
    parts = content.get("parts") or []
    return "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()


def _parse_grounded_sources(metadata: dict[str, Any]) -> list[GroundedSource]:
    chunks = metadata.get("groundingChunks") or metadata.get("grounding_chunks") or []
    sources: list[GroundedSource] = []
    for index, chunk in enumerate(chunks):
        web = chunk.get("web", {}) if isinstance(chunk, dict) else {}
        uri = str(web.get("uri", web.get("url", ""))).strip()
        title = str(web.get("title", "")).strip()
        sources.append(
            GroundedSource(
                source_id=f"g{index + 1}",
                title=title,
                url=uri,
            )
        )
    return sources


def _parse_grounding_supports(
    metadata: dict[str, Any],
    sources: list[GroundedSource],
) -> list[GroundedCitationSupport]:
    supports_payload = metadata.get("groundingSupports") or metadata.get("grounding_supports") or []
    supports: list[GroundedCitationSupport] = []
    for support in supports_payload:
        if not isinstance(support, dict):
            continue
        segment = support.get("segment") or {}
        text = str(segment.get("text", "")).strip()
        indices = support.get("groundingChunkIndices") or support.get("grounding_chunk_indices") or []
        source_ids = [
            sources[index].source_id
            for index in indices
            if isinstance(index, int) and 0 <= index < len(sources)
        ]
        if text or source_ids:
            supports.append(GroundedCitationSupport(text=text, source_ids=source_ids))
    return supports


def _evidence_from_supports(
    workstream: ResearchWorkstream,
    supports: list[GroundedCitationSupport],
    sources: list[GroundedSource],
    brief: ResearchBrief | None = None,
) -> list[dict[str, Any]]:
    pillar = workstream.pillar_name or workstream.title
    signal_map = PILLAR_SIGNALS.get(pillar, {})
    by_id = {source.source_id: source for source in sources}
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    entity_terms: set[str] = set()
    if brief and brief.entities:
        for entity in brief.entities:
            if entity.ticker:
                entity_terms.add(entity.ticker.strip().lower())
            lowered = (entity.name or "").strip().lower()
            if lowered:
                entity_terms.add(lowered)
            cleaned = re.sub(r"[^a-z0-9\\s]", " ", lowered)
            for token in cleaned.split():
                if len(token) >= 3 and token not in {"plc", "ltd", "inc", "corp", "co", "company"}:
                    entity_terms.add(token)

    def mentions_entity(text: str) -> bool:
        if not entity_terms:
            return True
        lowered = text.lower()
        return any(term in lowered for term in entity_terms)

    def support_confidence(text: str, source_title: str, url: str) -> float:
        conf = 0.35
        if re.search(r"\\b\\d+(?:\\.\\d+)?%\\b", text) or re.search(r"\\b\\d{4}\\b", text):
            conf += 0.1
        if re.search(r"\\b\\$\\s?\\d|\\b\\d+(?:\\.\\d+)?\\s?(?:billion|million|bn|m)\\b", text.lower()):
            conf += 0.1
        combined = f"{source_title}\n{url}".lower()
        if any(
            token in combined
            for token in (
                "investor",
                "annual report",
                "10-k",
                "20-f",
                "sec.gov",
                "results",
                "earnings",
            )
        ):
            conf += 0.1
        if mentions_entity(text):
            conf += 0.05
        return max(0.0, min(0.65, conf))

    max_total = int(os.getenv("GROUNDED_MAX_EVIDENCE_PER_WORKSTREAM", "30") or 30)
    max_per_signal = int(os.getenv("GROUNDED_MAX_EVIDENCE_PER_SIGNAL", "6") or 6)
    counts_by_signal: dict[str, int] = {}
    for support in supports:
        if not support.text.strip():
            continue
        if brief and not mentions_entity(support.text):
            continue
        text_l = support.text.lower()
        for signal_name, keywords in signal_map.items():
            if not any(keyword in text_l for keyword in keywords):
                continue
            if counts_by_signal.get(signal_name, 0) >= max_per_signal:
                continue
            source = (
                by_id.get(support.source_ids[0], GroundedSource(source_id="grounded"))
                if support.source_ids
                else GroundedSource(source_id="grounded")
            )
            dedupe_key = (signal_name, f"{source.url}::{support.text[:120]}".strip().lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            counts_by_signal[signal_name] = counts_by_signal.get(signal_name, 0) + 1
            evidence.append(
                {
                    "pillar_name": pillar,
                    "signal_name": signal_name,
                    "source_title": source.title,
                    "metric_name": "",
                    "metric_value": "",
                    "period": "",
                    "excerpt": support.text[:350],
                    "confidence": round(support_confidence(support.text, source.title, source.url), 3),
                    "source_kind": "gemini_grounded",
                    "source_url": source.url,
                }
            )
            if len(evidence) >= max_total:
                return evidence
    return evidence
