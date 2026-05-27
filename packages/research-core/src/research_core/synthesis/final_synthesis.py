"""Generate the final user-facing investment memo from scored research artifacts."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from langchain_openai import ChatOpenAI

from research_core.schemas.final_synthesis import FinalResearchSynthesis
from research_core.storage import artifact_store, ticker_artifact_key
from research_core.utils.logger import get_logger

logger = get_logger(__name__)

FINAL_SYNTHESIS_MODEL = os.getenv("FINAL_SYNTHESIS_MODEL", "gpt-4o-mini")
SYNTHESIS_RETRY_BACKOFF_SECONDS = (2, 5, 10)

FINAL_SYNTHESIS_SYSTEM = """You are an investment research editor writing for readers who are not finance professionals.

Transform the structured research input into a clear, trustworthy memo. Rules:
- Explain financial terms naturally when you use them; do not remove useful terms entirely.
- Never use internal pipeline phrases like "evidence hits" in user-facing text.
- Do not invent facts, numbers, or events that are not supported by the provided scorecard, pillar assessments, evidence, or sources.
- If evidence is thin, say so plainly and avoid strong claims.
- Surface conflicts when signals disagree (e.g. strong fundamentals vs weak technicals).
- For each pillar: lead with plain language; put denser metrics and methodology in technicalDetails.
- pillarTakeaways must include one object for every pillar name listed in the input "pillars_order" array, in that order. Copy each pillar's "score" from the input pillar assessments exactly.
- recommendationRationale and mainRisks should be distinct; rationale supports the recommendation, risks are what could go wrong.
- personalizedRecommendation must separate:
  - investmentQuality: the company's standalone attractiveness based only on research evidence.
  - investorFit: suitability for the provided user_context.
  - finalRecommendation: the resulting action after combining quality and fit.
- If user_context.profile_status is "anonymous_default", investorFit must be neutral/moderate, explicitly say no personal financial or risk profile was available, and avoid claiming the recommendation is personalized.
- If market_status.status is "pre_ipo_or_not_trading", do not say the stock is currently undervalued, trading below intrinsic value, technically bullish, or offering a market entry point. Use terms like proposed offer valuation, listing valuation, prospectus assumptions, and post-listing monitoring instead.

Respond with JSON only, matching the required schema."""

FINAL_SYNTHESIS_INSTRUCTION = """Using only the data below, produce the final investment memo JSON.

Input:
{payload}
"""


class FinalSynthesisError(RuntimeError):
    """Raised when the final synthesis step cannot produce valid output."""


def _trim_evidence(evidence_by_pillar: dict[str, list[dict]], per_pillar: int = 5) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for pillar, facts in evidence_by_pillar.items():
        trimmed: list[dict] = []
        for fact in (facts or [])[:per_pillar]:
            trimmed.append(
                {
                    "signal_name": fact.get("signal_name", ""),
                    "metric_name": fact.get("metric_name", ""),
                    "metric_value": fact.get("metric_value", ""),
                    "period": fact.get("period", ""),
                    "excerpt": (fact.get("excerpt") or "")[:400],
                    "confidence": fact.get("confidence"),
                    "source_title": fact.get("source_title", ""),
                }
            )
        out[pillar] = trimmed
    return out


def _trim_sources(sources_by_pillar: dict[str, list[dict]], per_pillar: int = 3) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for pillar, sources in sources_by_pillar.items():
        trimmed: list[dict] = []
        for src in (sources or [])[:per_pillar]:
            body = src.get("body") or ""
            snippet = src.get("snippet") or ""
            trimmed.append(
                {
                    "title": src.get("title", ""),
                    "link": src.get("link", ""),
                    "snippet": (snippet or body)[:320],
                    "source_kind": src.get("source_kind", ""),
                    "is_primary_source": bool(src.get("is_primary_source", False)),
                }
            )
        out[pillar] = trimmed
    return out


def build_synthesis_payload(
    company_name: str,
    ticker: str,
    scorecard: dict[str, Any],
    pillar_assessments: dict[str, dict[str, Any]],
    evidence_by_pillar: dict[str, list[dict]],
    sources_by_pillar: dict[str, list[dict]],
    pillars_order: list[str],
    user_financial_profile: dict[str, Any] | None = None,
    user_risk_profile: dict[str, Any] | None = None,
    investor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact, JSON-serializable context for the synthesis model."""
    assessments_compact: dict[str, Any] = {}
    for name, assessment in pillar_assessments.items():
        assessments_compact[name] = {
            "score": assessment.get("score"),
            "confidence": assessment.get("confidence"),
            "category": assessment.get("category", ""),
            "synopsis": assessment.get("synopsis", ""),
            "analysis": (assessment.get("analysis") or "")[:1200],
            "strengths": assessment.get("strengths", [])[:4],
            "gaps": assessment.get("gaps", [])[:6],
            "evidence_count": assessment.get("evidence_count", 0),
        }

    market_status = detect_market_status(
        scorecard=scorecard,
        evidence_by_pillar=evidence_by_pillar,
        sources_by_pillar=sources_by_pillar,
    )

    personalized = build_personalized_recommendation(
        scorecard=scorecard,
        user_financial_profile=user_financial_profile,
        user_risk_profile=user_risk_profile,
        investor_context=investor_context,
    )

    return {
        "company_name": company_name,
        "ticker": ticker,
        "pillars_order": pillars_order,
        "market_status": market_status,
        "user_context": {
            "profile_status": "personalized" if user_financial_profile or user_risk_profile else "anonymous_default",
            "financial_profile": user_financial_profile
            or {
                "income_stability": "unknown",
                "liquidity_needs": "unknown",
                "time_horizon": "unknown",
                "portfolio_concentration": "unknown",
            },
            "risk_profile": user_risk_profile
            or {
                "risk_tolerance": "unknown",
                "loss_tolerance": "unknown",
                "investing_experience": "unknown",
                "volatility_comfort": "unknown",
            },
            "recommendation_guidance": (
                "Use the provided financial and risk profile to assess suitability."
                if user_financial_profile or user_risk_profile
                else "No personal profile is available; produce a general research recommendation and keep investor fit neutral."
            ),
            "investor_context": investor_context or {},
        },
        "personalization_baseline": personalized,
        "scorecard": {
            "overall_score": scorecard.get("overall_score"),
            "confidence": scorecard.get("confidence"),
            "recommendation": scorecard.get("recommendation"),
            "reasoning": scorecard.get("reasoning", ""),
            "valuation_status": scorecard.get("valuation_status", ""),
            "technical_state": scorecard.get("technical_state", ""),
            "bullish_drivers": scorecard.get("bullish_drivers", [])[:5],
            "key_risks": scorecard.get("key_risks", [])[:5],
            "recommendation_confidence": scorecard.get("recommendation_confidence", ""),
            "pillar_scores": scorecard.get("pillar_scores", {}),
        },
        "pillar_assessments": assessments_compact,
        "evidence_by_pillar": _trim_evidence(evidence_by_pillar),
        "sources_by_pillar": _trim_sources(sources_by_pillar),
    }


def detect_market_status(
    *,
    scorecard: dict[str, Any],
    evidence_by_pillar: dict[str, list[dict]],
    sources_by_pillar: dict[str, list[dict]],
) -> dict[str, Any]:
    """Infer whether valuation should be written as pre-IPO/prospectus analysis.

    This is a language safety guard, not a securities master. The aim is to
    prevent market-price wording when the available evidence points to a future
    listing or offer document instead of an actively traded equity.
    """
    haystack_parts: list[str] = [
        str(scorecard.get("valuation_status", "")),
        str(scorecard.get("reasoning", "")),
    ]
    for facts in evidence_by_pillar.values():
        for fact in facts or []:
            haystack_parts.extend(
                [
                    str(fact.get("source_title", "")),
                    str(fact.get("metric_name", "")),
                    str(fact.get("metric_value", "")),
                    str(fact.get("excerpt", "")),
                ]
            )
    for sources in sources_by_pillar.values():
        for source in sources or []:
            haystack_parts.extend(
                [
                    str(source.get("title", "")),
                    str(source.get("snippet", "")),
                    str(source.get("body", "")),
                    str(source.get("link", "")),
                ]
            )
    haystack = " ".join(haystack_parts).lower()
    pre_ipo_terms = {
        "ipo",
        "initial public offering",
        "prospectus",
        "offer price",
        "proposed listing",
        "to be listed",
        "set to list",
        "not yet trading",
        "listing on",
        "admission to trading",
    }
    traded_terms = {"share price", "market price", "trading at", "trades at", "stock price", "52-week"}
    pre_ipo_matches = sorted(term for term in pre_ipo_terms if term in haystack)
    traded_matches = sorted(term for term in traded_terms if term in haystack)
    status = "pre_ipo_or_not_trading" if pre_ipo_matches and len(pre_ipo_matches) >= len(traded_matches) else "publicly_traded_or_unknown"
    return {
        "status": status,
        "signals": pre_ipo_matches[:8],
        "valuation_language_guardrail": (
            "Use offer/prospectus valuation language; avoid market-price claims."
            if status == "pre_ipo_or_not_trading"
            else "Market-price valuation language is allowed only when supported by evidence."
        ),
    }


def sanitize_market_status_language(synthesis: dict[str, Any], market_status: dict[str, Any]) -> dict[str, Any]:
    """Remove misleading market-price language for pre-IPO or not-yet-trading companies."""
    if market_status.get("status") != "pre_ipo_or_not_trading":
        return synthesis
    replacements = {
        "The stock is currently undervalued, presenting a potential buying opportunity.": "The proposed offer valuation may look attractive, but the shares are not yet trading and should be judged against the prospectus assumptions.",
        "the stock is currently undervalued": "the proposed offer valuation may look attractive",
        "stock is currently undervalued": "proposed offer valuation may look attractive",
        "stock is trading below its true worth": "offer price may be below estimated fair value, but this is not a live market-price signal",
        "trading below its true worth": "priced below estimated fair value on prospectus assumptions",
        "Valuation multiples suggest a favorable entry point for investors.": "Valuation multiples can help judge whether the offer price is reasonable before listing.",
        "favorable entry point": "potentially reasonable offer valuation",
        "Technical indicators suggest a bullish trend for the stock.": "There is limited technical analysis value before regular public trading begins.",
        "The stock is in a bullish trend": "Post-listing trading data is needed before calling a trend",
    }
    return _replace_strings(synthesis, replacements)


def _replace_strings(value: Any, replacements: dict[str, str]) -> Any:
    """Recursively replace risky phrases inside a JSON-like payload."""
    if isinstance(value, str):
        out = value
        for old, new in replacements.items():
            out = out.replace(old, new)
        return out
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_strings(item, replacements) for key, item in value.items()}
    return value


def build_personalized_recommendation(
    *,
    scorecard: dict[str, Any],
    user_financial_profile: dict[str, Any] | None = None,
    user_risk_profile: dict[str, Any] | None = None,
    investor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the deterministic personalization layer for final recommendations.

    The LLM writes the surrounding memo, but this profile-aware overlay keeps
    the action auditable: company quality stays separate from personal fit.
    """
    profile_status = "personalized" if user_financial_profile or user_risk_profile else "anonymous_default"
    quality_score = int(scorecard.get("overall_score") or 0)
    quality_rating = _quality_rating(quality_score)
    base_action = str(scorecard.get("recommendation") or "Watchlist")
    confidence = str(scorecard.get("recommendation_confidence") or "medium")
    financial = user_financial_profile or {}
    risk = user_risk_profile or {}
    context = investor_context or {}

    if profile_status == "anonymous_default":
        fit_score = 55
        fit_rating = "Neutral until profiled"
        fit_rationale = ["No personal financial or risk profile was available, so suitability stays neutral."]
        constraints = ["Complete onboarding before treating this as personalized."]
        final_action = base_action
        suitability_notes = ["This is a general research recommendation, not a profile-specific recommendation."]
    else:
        fit_score = 72
        fit_rationale = ["The recommendation used the saved financial profile and risk profile."]
        constraints: list[str] = []
        resilience = str(financial.get("financialResilience", "")).lower()
        tolerance = str(risk.get("riskTolerance", "")).lower()
        capacity = str(risk.get("riskCapacity", "")).lower()
        horizon = str(risk.get("timeHorizon") or context.get("timeHorizon") or "").lower()
        restricted = context.get("restrictedSectors") or []

        if resilience == "low":
            fit_score -= 22
            constraints.append("Financial resilience is low, so speculative or volatile positions should be sized conservatively.")
        if tolerance == "low":
            fit_score -= 18
            constraints.append("Risk tolerance is low, so large drawdowns may be hard to tolerate.")
        if capacity == "low":
            fit_score -= 20
            constraints.append("Risk capacity is low, so the recommendation should prioritize downside protection.")
        if "10" in horizon or "5_to_10" in horizon:
            fit_score += 6
            fit_rationale.append("A longer time horizon can support some business-cycle volatility.")
        if restricted:
            fit_score -= 8
            constraints.append(f"Restricted sectors/preferences should be checked before acting: {', '.join(map(str, restricted))}.")
        if float(scorecard.get("confidence") or 0) < 0.55:
            fit_score -= 10
            constraints.append("Research confidence is limited, so the action should be more cautious.")

        fit_score = max(0, min(100, fit_score))
        fit_rating = _fit_rating(fit_score)
        final_action = _personalized_action(base_action, quality_score, fit_score)
        suitability_notes = constraints or ["No major profile constraints changed the standalone research view."]

    return {
        "investmentQuality": {
            "score": quality_score,
            "rating": quality_rating,
            "rationale": list(scorecard.get("bullish_drivers") or [])[:3]
            or [str(scorecard.get("reasoning") or "Standalone quality is based on the six-pillar research score.")],
            "confidence": confidence,
        },
        "investorFit": {
            "score": fit_score,
            "rating": fit_rating,
            "rationale": fit_rationale,
            "constraints": constraints,
            "profileBasis": profile_status,
        },
        "finalRecommendation": {
            "action": final_action,
            "confidence": "high" if confidence == "high" and fit_score >= 65 else "medium" if fit_score >= 45 else "low",
            "explanation": (
                f"Standalone research points to {base_action}, while the saved profile produces a {fit_rating.lower()} fit."
                if profile_status == "personalized"
                else f"Standalone research points to {base_action}; no profile was available to personalize it."
            ),
            "suitabilityNotes": suitability_notes,
        },
    }


def _quality_rating(score: int) -> str:
    if score >= 75:
        return "Strong"
    if score >= 60:
        return "Mixed but constructive"
    if score >= 45:
        return "Speculative"
    return "Weak"


def _fit_rating(score: int) -> str:
    if score >= 75:
        return "Strong fit"
    if score >= 60:
        return "Reasonable fit"
    if score >= 45:
        return "Cautious fit"
    return "Poor fit"


def _personalized_action(base_action: str, quality_score: int, fit_score: int) -> str:
    if fit_score < 45:
        return "Watchlist" if quality_score >= 70 else "Avoid"
    if fit_score < 60 and base_action in {"Buy", "Strong Buy"}:
        return "Watchlist"
    return base_action


class FinalSynthesisGenerator:
    """LLM-only final memo generation with retries."""

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or FINAL_SYNTHESIS_MODEL

    def generate(
        self,
        company_name: str,
        ticker: str,
        scorecard: dict[str, Any],
        pillar_assessments: dict[str, dict[str, Any]],
        evidence_by_pillar: dict[str, list[dict]],
        sources_by_pillar: dict[str, list[dict]],
        pillars_order: list[str],
        user_financial_profile: dict[str, Any] | None = None,
        user_risk_profile: dict[str, Any] | None = None,
        investor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise FinalSynthesisError("OPENAI_API_KEY is not set; final synthesis requires an LLM.")

        payload = build_synthesis_payload(
            company_name=company_name,
            ticker=ticker,
            scorecard=scorecard,
            pillar_assessments=pillar_assessments,
            evidence_by_pillar=evidence_by_pillar,
            sources_by_pillar=sources_by_pillar,
            pillars_order=pillars_order,
            user_financial_profile=user_financial_profile,
            user_risk_profile=user_risk_profile,
            investor_context=investor_context,
        )
        market_status = dict(payload.get("market_status") or {})
        user_message = FINAL_SYNTHESIS_INSTRUCTION.replace(
            "{payload}", json.dumps(payload, indent=2, ensure_ascii=False)
        )

        last_error: BaseException | None = None
        for attempt in range(len(SYNTHESIS_RETRY_BACKOFF_SECONDS)):
            try:
                model = ChatOpenAI(model=self._model_name, temperature=0)
                structured = model.with_structured_output(FinalResearchSynthesis)
                parsed = structured.invoke(
                    [
                        ("system", FINAL_SYNTHESIS_SYSTEM),
                        ("human", user_message),
                    ]
                )
                if isinstance(parsed, FinalResearchSynthesis):
                    out = parsed.model_dump(mode="json", by_alias=True)
                    out["personalizedRecommendation"] = build_personalized_recommendation(
                        scorecard=scorecard,
                        user_financial_profile=user_financial_profile,
                        user_risk_profile=user_risk_profile,
                        investor_context=investor_context,
                    )
                    out["personalizedRecommendation"]["synthesisSource"] = "llm_with_guardrails"
                    out["marketStatus"] = market_status
                    out = sanitize_market_status_language(out, market_status)
                    return out
                if isinstance(parsed, dict):
                    out = FinalResearchSynthesis.model_validate(parsed).model_dump(mode="json", by_alias=True)
                    out["personalizedRecommendation"] = build_personalized_recommendation(
                        scorecard=scorecard,
                        user_financial_profile=user_financial_profile,
                        user_risk_profile=user_risk_profile,
                        investor_context=investor_context,
                    )
                    out["personalizedRecommendation"]["synthesisSource"] = "llm_with_guardrails"
                    out["marketStatus"] = market_status
                    out = sanitize_market_status_language(out, market_status)
                    return out
                raise FinalSynthesisError(f"Unexpected structured output type: {type(parsed)}")
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Final synthesis attempt %s failed: %s",
                    attempt + 1,
                    exc,
                )
                if attempt < len(SYNTHESIS_RETRY_BACKOFF_SECONDS) - 1:
                    time.sleep(float(SYNTHESIS_RETRY_BACKOFF_SECONDS[attempt]))

        raise FinalSynthesisError(
            f"Final synthesis failed after {len(SYNTHESIS_RETRY_BACKOFF_SECONDS)} attempts: {last_error}"
        ) from last_error

    def persist_artifact(self, ticker: str, synthesis: dict[str, Any]) -> str:
        """Write optional audit artifact under storage/artifacts/{TICKER}/synthesis/."""
        store = artifact_store()
        return store.write_json(ticker_artifact_key(ticker, "synthesis", "final_synthesis.json"), synthesis)
