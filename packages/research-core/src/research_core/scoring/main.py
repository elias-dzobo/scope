"""Deterministic analysis and scoring layer for stock research artifacts."""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from typing import Any, Callable

from langchain_openai import ChatOpenAI

from scope_api.observability.metrics import parse_llm_usage, record_llm_call
from scope_api.observability.telemetry import observe_span
from research_core.prompts.tool_prompts import EVIDENCE_EXTRACTION_PROMPT
from research_core.schemas.tool_schema import (
    EvidenceFact,
    PillarAssessment,
    PillarEvidenceExtractionResponse,
    StockScorecard,
)
from research_core.utils.logger import get_logger

logger = get_logger(__name__)
ProgressCallback = Callable[[str, dict[str, Any]], None]


PILLAR_SIGNALS: dict[str, dict[str, list[str]]] = {
    "Macro & Industry": {
        "Secular Trends": ["trend", "growth", "demand", "outlook", "cagr", "tam"],
        "Market Structure": ["market share", "industry", "competition", "regulation"],
    },
    "Economic Moat": {
        "Switching Costs": ["switching", "retention", "contract", "renewal"],
        "Competitive Advantage": ["brand", "patent", "network effect", "cost advantage"],
    },
    "Financial Engine": {
        "Profitability": ["roic", "margin", "ebitda", "eps", "profit"],
        "Cash and Leverage": ["free cash flow", "fcf", "debt", "debt/ebitda", "balance sheet"],
    },
    "Management & Capital Allocation": {
        "Capital Allocation": ["buyback", "dividend", "acquisition", "allocation"],
        "Governance": ["insider", "ownership", "board", "governance", "management"],
    },
    "Valuation": {
        "Multiples": ["p/e", "ev/ebitda", "multiple", "valuation", "price target"],
        "Intrinsic Value": ["dcf", "fair value", "discount", "upside", "margin of safety"],
    },
    "Technical Analysis": {
        "Trend and Momentum": ["moving average", "breakout", "rsi", "momentum", "uptrend"],
        "Volume and Relative Strength": ["volume", "accumulation", "relative strength", "support", "resistance"],
    },
}

PILLAR_WEIGHTS: dict[str, float] = {
    "Macro & Industry": 0.15,
    "Economic Moat": 0.2,
    "Financial Engine": 0.25,
    "Management & Capital Allocation": 0.15,
    "Valuation": 0.15,
    "Technical Analysis": 0.1,
}

SCORECARD_SYNTHESIS_MODEL = os.getenv("SCORECARD_SYNTHESIS_MODEL", "gpt-4o-mini")
SCORECARD_SYNTHESIS_SYSTEM = """You are Scope's investment research scoring agent.

You receive:
- deterministicBaseline: the current rules-based scorecard
- pillarAssessments: six-pillar research assessments
- evidenceByPillar: extracted evidence facts

Your task is to return a StockScorecard JSON object with the exact same field
shape as the baseline. You may adjust scores or recommendation only when the
evidence and pillar assessments support it.

Rules:
- Keep scores between 0 and 100 and confidence between 0 and 1.
- Do not invent facts, sources, financial metrics, or personal profile details.
- Penalize weak evidence coverage, unsupported claims, and low confidence.
- Keep recommendation within practical investment-research language such as
  Good Buy, Watch / Accumulate on Weakness, Wait for Dip, Hold / Not Attractive Now,
  Avoid, or Insufficient Data.
- Separate business quality from valuation. A strong business can still be a
  Wait for Dip or Hold if valuation evidence is weak/expensive.
- If evidence is thin, prefer Insufficient Data or a low-confidence cautious recommendation.
"""


def _build_entity_terms(stock_name: str, ticker: str = "") -> set[str]:
    terms: set[str] = set()
    if ticker.strip():
        terms.add(ticker.strip().lower())

    lowered = stock_name.lower().strip()
    if lowered:
        terms.add(lowered)

    cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
    stop = {"plc", "ltd", "bank", "group", "company", "corporation", "ghana", "limited"}
    for token in cleaned.split():
        if len(token) >= 3 and token not in stop:
            terms.add(token)
    return terms


def _mentions_entity(text: str, entity_terms: set[str]) -> bool:
    if not entity_terms:
        return True
    lowered = text.lower()
    return any(term in lowered for term in entity_terms)


def _extract_signal_hits(text: str, signal_keywords: dict[str, list[str]]) -> list[str]:
    text_l = text.lower()
    hits: list[str] = []
    for signal_name, keywords in signal_keywords.items():
        if any(keyword in text_l for keyword in keywords):
            hits.append(signal_name)
    return hits


def _parse_model_json_response(response: Any) -> dict[str, Any]:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return json.loads(content)

    if isinstance(content, list):
        text_blocks = [item.get("text", "") for item in content if isinstance(item, dict)]
        if text_blocks:
            return json.loads("".join(text_blocks))

    if isinstance(content, dict):
        return content

    raise ValueError(f"Unsupported model response content type: {type(content)!r}")


def _scorecard_llm_enabled(llm_enabled: bool | None) -> bool:
    """Return whether LLM scorecard synthesis should run."""
    if llm_enabled is not None:
        return llm_enabled
    enabled = os.getenv("SCORECARD_LLM_ENABLED", "true").strip().lower()
    return enabled not in {"0", "false", "no", "off"} and bool(os.getenv("OPENAI_API_KEY", "").strip())


def _format_documents_for_prompt(documents: list[dict]) -> str:
    chunks: list[str] = []
    for idx, sample in enumerate(documents, start=1):
        title = sample.get("title", "")
        body = sample.get("body", "")[:1600]
        link = sample.get("link", "")
        chunks.append(f"[Doc {idx}] Title: {title}\nURL: {link}\nBody: {body}")
    return "\n\n".join(chunks)


def _chunk_documents(documents: list[dict], chunk_size: int = 5) -> list[list[dict]]:
    return [documents[i : i + chunk_size] for i in range(0, len(documents), chunk_size)]


def _extract_pillar_evidence_llm_chunk(
    stock_name: str,
    pillar_name: str,
    documents: list[dict],
    allowed_signals: list[str],
) -> list[dict]:
    model_name = "gpt-4o-mini"
    prompt = EVIDENCE_EXTRACTION_PROMPT.replace("{stock_name}", stock_name).replace(
        "{pillar_name}", pillar_name
    )
    prompt = prompt.replace("{allowed_signals}", ", ".join(allowed_signals))
    prompt = prompt.replace("{documents}", _format_documents_for_prompt(documents))

    with observe_span(
        "agent.extract_evidence_chunk",
        {
            "operation": "extract_evidence",
            "model": model_name,
            "pillar": pillar_name,
        },
    ):
        started = time.perf_counter()
        model = ChatOpenAI(
            model=model_name,
            temperature=0,
            response_format=PillarEvidenceExtractionResponse,
        )
        response = model.invoke(prompt)
    duration_seconds = time.perf_counter() - started
    usage = getattr(response, "usage_metadata", None)
    input_tokens, output_tokens = parse_llm_usage(usage)
    record_llm_call(
        operation="extract_evidence",
        provider="openai",
        model=model_name,
        success=True,
        duration_seconds=duration_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    parsed = _parse_model_json_response(response)
    validated = PillarEvidenceExtractionResponse.model_validate(parsed).model_dump()
    return validated["facts"]


def _normalize_and_filter_facts(
    pillar_name: str,
    facts: list[dict],
    allowed_signals: set[str],
    entity_terms: set[str],
) -> list[dict]:
    normalized: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for fact in facts:
        signal_name = fact.get("signal_name", "").strip()
        if signal_name not in allowed_signals:
            continue

        source_title = fact.get("source_title", "")
        excerpt = fact.get("excerpt", "")
        if not _mentions_entity(f"{source_title}\n{excerpt}", entity_terms):
            continue

        confidence = float(fact.get("confidence", 0.0) or 0.0)
        confidence = max(0.0, min(1.0, confidence))

        key = (
            signal_name,
            source_title.strip().lower(),
            fact.get("metric_value", "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)

        record = EvidenceFact(
            pillar_name=pillar_name,
            signal_name=signal_name,
            source_title=source_title,
            metric_name=fact.get("metric_name", ""),
            metric_value=fact.get("metric_value", ""),
            period=fact.get("period", ""),
            excerpt=excerpt[:350],
            confidence=confidence,
        )
        normalized.append(record.model_dump())

    return normalized


def _extract_pillar_evidence_llm(
    stock_name: str,
    ticker: str,
    pillar_name: str,
    documents: list[dict],
) -> list[dict]:
    allowed_signals = list(PILLAR_SIGNALS.get(pillar_name, {}).keys())
    if not documents or not allowed_signals:
        return []

    entity_terms = _build_entity_terms(stock_name, ticker)
    all_facts: list[dict] = []
    for chunk in _chunk_documents(documents, chunk_size=5):
        chunk_facts = _extract_pillar_evidence_llm_chunk(stock_name, pillar_name, chunk, allowed_signals)
        all_facts.extend(chunk_facts)

    return _normalize_and_filter_facts(
        pillar_name=pillar_name,
        facts=all_facts,
        allowed_signals=set(allowed_signals),
        entity_terms=entity_terms,
    )


def _extract_pillar_evidence_deterministic(
    stock_name: str,
    ticker: str,
    pillar_name: str,
    documents: list[dict],
) -> list[dict]:
    """Extract evidence facts from scraped documents using deterministic signal matching."""
    pillar_signals = PILLAR_SIGNALS.get(pillar_name, {})
    facts: list[dict] = []
    entity_terms = _build_entity_terms(stock_name, ticker)

    for sample in documents:
        title = sample.get("title", "")
        body = sample.get("body", "")
        text = f"{title}\n{body[:5000]}"
        if not _mentions_entity(text, entity_terms):
            continue
        signal_hits = _extract_signal_hits(text, pillar_signals)

        for signal_name in signal_hits:
            confidence = min(1.0, 0.45 + (0.1 * len(signal_hits)))
            fact = EvidenceFact(
                pillar_name=pillar_name,
                signal_name=signal_name,
                source_title=title,
                metric_name="",
                metric_value="",
                period="",
                excerpt=body[:280],
                confidence=confidence,
            )
            facts.append(fact.model_dump())

    return facts


def extract_evidence_facts(
    scraped_results: dict[str, list[dict]],
    stock_name: str = "",
    ticker: str = "",
    llm_enabled: bool | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, list[dict]]:
    evidence_by_pillar: dict[str, list[dict]] = {}
    should_use_llm = llm_enabled if llm_enabled is not None else bool(os.getenv("OPENAI_API_KEY"))
    total_pillars = len(scraped_results)
    completed_pillars = 0

    for pillar_name, documents in scraped_results.items():
        if should_use_llm and documents:
            try:
                facts = _extract_pillar_evidence_llm(stock_name, ticker, pillar_name, documents)
                evidence_by_pillar[pillar_name] = facts
                completed_pillars += 1
                if progress_callback:
                    progress_callback(
                        "07_extract",
                        {
                            "status": "running",
                            "current_substep": f"extracting pillar {completed_pillars}/{total_pillars}",
                            "stage_current": completed_pillars,
                            "stage_total": total_pillars,
                            "stage_progress": round((completed_pillars / total_pillars) * 100, 1) if total_pillars else 0,
                            "pillar": pillar_name,
                            "evidence_count": len(facts),
                        },
                    )
                continue
            except Exception:
                logger.exception("LLM evidence extraction failed for pillar=%s; using fallback", pillar_name)

        evidence_by_pillar[pillar_name] = _extract_pillar_evidence_deterministic(
            stock_name=stock_name,
            ticker=ticker,
            pillar_name=pillar_name,
            documents=documents,
        )
        completed_pillars += 1
        if progress_callback:
            progress_callback(
                "07_extract",
                {
                    "status": "running",
                    "current_substep": f"extracting pillar {completed_pillars}/{total_pillars}",
                    "stage_current": completed_pillars,
                    "stage_total": total_pillars,
                    "stage_progress": round((completed_pillars / total_pillars) * 100, 1) if total_pillars else 0,
                    "pillar": pillar_name,
                    "evidence_count": len(evidence_by_pillar[pillar_name]),
                },
            )

    return evidence_by_pillar


def _pillar_category(score: int, evidence_count: int, confidence: float) -> str:
    if evidence_count < 2 or confidence < 0.35:
        return "Insufficient Data"
    if score >= 75:
        return "Bullish"
    if score >= 60:
        return "Neutral"
    if score >= 45:
        return "Cautious"
    return "Weak"


def _pillar_synopsis(
    pillar_name: str,
    score: int,
    confidence: float,
    category: str,
    strengths: list[str],
    gaps: list[str],
) -> str:
    lead = strengths[0] if strengths else "limited confirmed evidence"
    risk = gaps[0] if gaps else "no major gap"
    return (
        f"{pillar_name} is categorized as {category.lower()} with score {score}/100 "
        f"and confidence {int(round(confidence * 100))}%. "
        f"Primary support: {lead}. Main gap: {risk}."
    )


def _pillar_analysis(
    pillar_name: str,
    score: int,
    confidence: float,
    category: str,
    strengths: list[str],
    gaps: list[str],
    evidence_count: int,
    facts: list[dict[str, Any]],
) -> str:
    confidence_pct = int(round(confidence * 100))
    if evidence_count == 0:
        return (
            f"{pillar_name} could not be supported with enough confirmed evidence in the current run. "
            f"The pillar therefore remains {category.lower()} at {score}/100 with confidence {confidence_pct}%. "
            f"The most important missing areas are {', '.join(gaps[:2]).lower() if gaps else 'core pillar signals'}."
        )

    support_text = strengths[0] if strengths else "limited support"
    secondary_support = strengths[1] if len(strengths) > 1 else ""
    gap_text = gaps[0] if gaps else "no major unresolved gap"

    narrative = (
        f"{pillar_name} is currently assessed as {category.lower()} with a score of {score}/100 and confidence "
        f"of {confidence_pct}%. The strongest confirmed support came from {support_text.lower()}."
    )
    if secondary_support:
        narrative += f" Secondary support came from {secondary_support.lower()}."
    evidence_highlights = _build_evidence_highlights(facts)
    if evidence_highlights:
        narrative += f" Key observed evidence included {evidence_highlights}."
    narrative += f" The main constraint on a stronger view is {gap_text.lower()}."
    if evidence_count < 3:
        narrative += " Evidence depth is still thin, so the score should be treated as directional rather than fully confirmed."
    elif confidence >= 0.75:
        narrative += " Source coverage and signal consistency were strong enough to support a higher-confidence view."
    return narrative


def _build_evidence_highlights(facts: list[dict[str, Any]], limit: int = 2) -> str:
    highlights: list[str] = []
    for fact in sorted(
        facts,
        key=lambda item: (float(item.get("confidence", 0.0) or 0.0), bool(item.get("metric_value"))),
        reverse=True,
    ):
        metric_name = str(fact.get("metric_name", "")).strip()
        metric_value = str(fact.get("metric_value", "")).strip()
        signal_name = str(fact.get("signal_name", "")).strip()
        source_title = str(fact.get("source_title", "")).strip()
        period = str(fact.get("period", "")).strip()

        if metric_name and metric_value:
            summary = f"{metric_name} at {metric_value}"
        elif metric_value:
            summary = metric_value
        else:
            excerpt = str(fact.get("excerpt", "")).strip()
            summary = excerpt[:80].rstrip(" .,;:")

        if not summary:
            continue
        if signal_name:
            summary = f"{signal_name.lower()} evidence showing {summary}"
        if period:
            summary += f" ({period})"
        if source_title:
            summary += f" from {source_title}"
        highlights.append(summary)
        if len(highlights) >= limit:
            break

    if not highlights:
        return ""
    if len(highlights) == 1:
        return highlights[0]
    return f"{highlights[0]}, and {highlights[1]}"


def _compact_evidence_for_scorecard(evidence_by_pillar: dict[str, list[dict]], limit_per_pillar: int = 5) -> dict[str, list[dict]]:
    """Trim evidence facts before sending them to the scorecard synthesizer."""
    compact: dict[str, list[dict]] = {}
    for pillar, facts in (evidence_by_pillar or {}).items():
        compact[pillar] = []
        for fact in facts[:limit_per_pillar]:
            compact[pillar].append(
                {
                    "signal_name": fact.get("signal_name", ""),
                    "source_title": fact.get("source_title", ""),
                    "metric_name": fact.get("metric_name", ""),
                    "metric_value": fact.get("metric_value", ""),
                    "period": fact.get("period", ""),
                    "excerpt": str(fact.get("excerpt", ""))[:280],
                    "confidence": fact.get("confidence", 0.0),
                    "source_kind": fact.get("source_kind", ""),
                }
            )
    return compact


def _build_stock_scorecard_llm(
    *,
    stock_name: str,
    ticker: str,
    pillar_assessments: dict[str, dict],
    evidence_by_pillar: dict[str, list[dict]],
    deterministic_scorecard: dict[str, Any],
) -> dict[str, Any]:
    """Ask an LLM to synthesize the final scorecard from evidence and baseline."""
    payload = {
        "stockName": stock_name,
        "ticker": ticker,
        "deterministicBaseline": deterministic_scorecard,
        "pillarAssessments": pillar_assessments,
        "evidenceByPillar": _compact_evidence_for_scorecard(evidence_by_pillar),
    }
    with observe_span(
        "agent.scorecard_synthesis",
        {
            "operation": "scorecard_synthesis",
            "model": SCORECARD_SYNTHESIS_MODEL,
            "ticker": ticker,
        },
    ):
        started = time.perf_counter()
        model = ChatOpenAI(model=SCORECARD_SYNTHESIS_MODEL, temperature=0.1)
        structured = model.with_structured_output(StockScorecard)
        parsed = structured.invoke(
            [
                ("system", SCORECARD_SYNTHESIS_SYSTEM),
                ("human", json.dumps(payload, indent=2, ensure_ascii=False)),
            ]
        )
    duration_seconds = time.perf_counter() - started
    usage = getattr(parsed, "usage_metadata", None)
    input_tokens, output_tokens = parse_llm_usage(usage)
    record_llm_call(
        operation="scorecard_synthesis",
        provider="openai",
        model=SCORECARD_SYNTHESIS_MODEL,
        success=True,
        duration_seconds=duration_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    if isinstance(parsed, StockScorecard):
        scorecard = parsed.model_dump()
    elif isinstance(parsed, dict):
        scorecard = StockScorecard.model_validate(parsed).model_dump()
    else:
        scorecard = StockScorecard.model_validate(_parse_model_json_response(parsed)).model_dump()
    scorecard["scorecard_source"] = "llm_assisted"
    scorecard["deterministic_baseline"] = deterministic_scorecard
    return scorecard


def assess_pillars(evidence_by_pillar: dict[str, list[dict]]) -> dict[str, dict]:
    """Create pillar-level assessments using evidence density and signal coverage."""
    assessments: dict[str, dict] = {}

    for pillar_name, facts in evidence_by_pillar.items():
        configured_signals = list(PILLAR_SIGNALS.get(pillar_name, {}).keys())
        configured_signal_set = set(configured_signals)
        total_signals = len(configured_signals) if configured_signals else 1

        sanitized_facts = [fact for fact in facts if fact.get("signal_name") in configured_signal_set]
        evidence_count = len(sanitized_facts)
        source_diversity = len(
            {fact.get("source_title", "") for fact in sanitized_facts if fact.get("source_title")}
        )

        signal_counter = Counter(fact["signal_name"] for fact in sanitized_facts)
        covered_signals = set(signal_counter.keys())
        coverage_ratio = min(1.0, len(covered_signals) / total_signals)
        avg_confidence = (
            sum(float(fact.get("confidence", 0.0)) for fact in sanitized_facts) / evidence_count
            if evidence_count
            else 0.0
        )

        density_ratio = min(evidence_count / 8, 1.0)
        diversity_ratio = min(source_diversity / 4, 1.0)
        raw_score = (35 * coverage_ratio) + (20 * density_ratio) + (10 * diversity_ratio) + (35 * avg_confidence)
        if evidence_count < 2:
            raw_score = min(raw_score, 50)
        score = int(round(min(100, raw_score)))
        confidence = round(min(1.0, (0.3 * coverage_ratio) + (0.55 * avg_confidence) + (0.15 * diversity_ratio)), 3)

        strengths = [f"{name}: {count} evidence hits" for name, count in signal_counter.most_common(2)]
        gaps = [name for name in configured_signals if name not in covered_signals]
        category = _pillar_category(score, evidence_count, confidence)
        synopsis = _pillar_synopsis(pillar_name, score, confidence, category, strengths, gaps)
        analysis = _pillar_analysis(
            pillar_name,
            score,
            confidence,
            category,
            strengths,
            gaps,
            evidence_count,
            sanitized_facts,
        )

        assessment = PillarAssessment(
            pillar_name=pillar_name,
            score=score,
            confidence=confidence,
            strengths=strengths,
            gaps=gaps,
            evidence_count=evidence_count,
            category=category,
            synopsis=synopsis,
            analysis=analysis,
        )
        assessments[pillar_name] = assessment.model_dump()

    return assessments


def build_stock_scorecard(
    stock_name: str,
    ticker: str,
    pillar_assessments: dict[str, dict],
    evidence_by_pillar: dict[str, list[dict]] | None = None,
    llm_enabled: bool | None = None,
) -> dict:
    """Aggregate pillar assessments into a final scorecard and recommendation.

    The deterministic baseline always runs first. When explicitly enabled by
    `llm_enabled=True` or `SCORECARD_LLM_ENABLED=true`, an LLM can revise the
    scorecard from the baseline plus evidence. Failures fall back to the
    deterministic scorecard so the research pipeline remains stable.
    """
    if not pillar_assessments:
        empty_scorecard = StockScorecard(
            stock_name=stock_name,
            ticker=ticker,
            pillar_scores={},
            overall_score=0,
            confidence=0.0,
            recommendation="Insufficient Data",
            reasoning="No pillar assessments were generated.",
            pillar_classifications={},
            valuation_status="Unknown",
            technical_state="Unknown",
            bullish_drivers=[],
            key_risks=[],
            invalidation_conditions=[],
            recommendation_confidence="Low",
        )
        out = empty_scorecard.model_dump()
        out["scorecard_source"] = "deterministic_fallback"
        return out

    pillar_scores = {pillar: int(assessment["score"]) for pillar, assessment in pillar_assessments.items()}
    pillar_confidences = [float(assessment["confidence"]) for assessment in pillar_assessments.values()]
    available_weights = {pillar: PILLAR_WEIGHTS.get(pillar, 0.0) for pillar in pillar_scores}
    weight_sum = sum(available_weights.values()) or 1.0
    normalized_weights = {pillar: weight / weight_sum for pillar, weight in available_weights.items()}

    weighted_score = sum(pillar_scores[pillar] * normalized_weights[pillar] for pillar in pillar_scores)
    overall_score = int(round(weighted_score))
    overall_confidence = round(sum(pillar_confidences) / len(pillar_confidences), 3)

    pillar_classifications: dict[str, str] = {}
    for pillar, score in pillar_scores.items():
        if score >= 75:
            pillar_classifications[pillar] = "Strong"
        elif score >= 60:
            pillar_classifications[pillar] = "Neutral"
        else:
            pillar_classifications[pillar] = "Weak"

    evidence_by_pillar = evidence_by_pillar or {}
    if evidence_by_pillar:
        evidence_counts = {pillar: len(evidence_by_pillar.get(pillar, [])) for pillar in pillar_scores}
    else:
        evidence_counts = {
            pillar: int(assessment.get("evidence_count", 0))
            for pillar, assessment in pillar_assessments.items()
        }
    pillars_with_sufficient_data = sum(1 for count in evidence_counts.values() if count >= 2)

    valuation_score = pillar_scores.get("Valuation", 0)
    technical_score = pillar_scores.get("Technical Analysis", 0)
    financial_score = pillar_scores.get("Financial Engine", 0)
    moat_score = pillar_scores.get("Economic Moat", 0)

    if evidence_counts.get("Valuation", 0) < 2:
        valuation_status = "Unknown"
    elif valuation_score >= 75:
        valuation_status = "Undervalued"
    elif valuation_score >= 60:
        valuation_status = "Fairly Valued"
    else:
        valuation_status = "Overvalued"

    if evidence_counts.get("Technical Analysis", 0) < 2:
        technical_state = "Unknown"
    elif technical_score >= 70:
        technical_state = "Bullish Trend"
    elif technical_score >= 55:
        technical_state = "Neutral Trend"
    else:
        technical_state = "Bearish Trend"

    low_confidence = overall_confidence < 0.55
    if pillars_with_sufficient_data < 3:
        recommendation = "Insufficient Data"
    elif overall_score >= 75 and financial_score >= 70 and moat_score >= 65:
        if valuation_status == "Overvalued":
            recommendation = "Wait for Dip"
        elif technical_state == "Bearish Trend":
            recommendation = "Watch / Accumulate on Weakness"
        else:
            recommendation = "Good Buy"
    elif overall_score >= 62:
        if valuation_status == "Overvalued":
            recommendation = "Hold / Not Attractive Now"
        else:
            recommendation = "Watch / Accumulate on Weakness"
    else:
        recommendation = "Avoid"

    if low_confidence and recommendation in {"Good Buy", "Wait for Dip"}:
        recommendation = "Watch / Accumulate on Weakness"

    recommendation_confidence = "High" if overall_confidence >= 0.75 else "Medium" if overall_confidence >= 0.6 else "Low"

    strongest = max(pillar_scores, key=pillar_scores.get)
    weakest = min(pillar_scores, key=pillar_scores.get)
    reasoning = (
        f"Strongest pillar: {strongest}. Weakest pillar: {weakest}. "
        f"Valuation is {valuation_status.lower()} and technicals indicate a {technical_state.lower()}."
    )

    bullish_drivers: list[str] = []
    key_risks: list[str] = []
    for pillar, assessment in sorted(
        pillar_assessments.items(),
        key=lambda item: item[1]["score"],
        reverse=True,
    ):
        if assessment.get("strengths") and len(bullish_drivers) < 3:
            bullish_drivers.append(f"{pillar}: {assessment['strengths'][0]}")
        if assessment.get("gaps") and len(key_risks) < 3:
            key_risks.append(f"{pillar}: {assessment['gaps'][0]}")

    invalidation_conditions = [
        "Financial Engine score drops below 60 on next earnings cycle.",
        "Valuation remains overvalued while momentum weakens.",
        "Key moat signals deteriorate across recent filings.",
    ]

    scorecard = StockScorecard(
        stock_name=stock_name,
        ticker=ticker,
        pillar_scores=pillar_scores,
        overall_score=overall_score,
        confidence=overall_confidence,
        recommendation=recommendation,
        reasoning=reasoning,
        pillar_classifications=pillar_classifications,
        valuation_status=valuation_status,
        technical_state=technical_state,
        bullish_drivers=bullish_drivers,
        key_risks=key_risks,
        invalidation_conditions=invalidation_conditions,
        recommendation_confidence=recommendation_confidence,
    )
    deterministic_scorecard = scorecard.model_dump()
    deterministic_scorecard["scorecard_source"] = "deterministic"

    if not _scorecard_llm_enabled(llm_enabled):
        return deterministic_scorecard

    try:
        return _build_stock_scorecard_llm(
            stock_name=stock_name,
            ticker=ticker,
            pillar_assessments=pillar_assessments,
            evidence_by_pillar=evidence_by_pillar,
            deterministic_scorecard=deterministic_scorecard,
        )
    except Exception as exc:
        logger.exception("LLM scorecard synthesis failed for ticker=%s; using deterministic scorecard", ticker)
        deterministic_scorecard["scorecard_source"] = "deterministic_fallback"
        deterministic_scorecard["scorecard_error"] = str(exc)
        return deterministic_scorecard
