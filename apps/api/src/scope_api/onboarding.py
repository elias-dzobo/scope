"""Onboarding profile synthesis and routes."""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from scope_api import db
from scope_api.auth.dependencies import require_current_user
from scope_api.auth.models import AuthUser
from research_core.utils.logger import get_logger

logger = get_logger(__name__)

PROFILE_SYNTHESIS_MODEL = os.getenv("PROFILE_SYNTHESIS_MODEL", "gpt-4o-mini")

PROFILE_SYNTHESIS_SYSTEM = """You are an investment onboarding analyst.

Write a concise, user-facing profile explanation from structured onboarding data.
Rules:
- Do not provide personal financial advice.
- Do not invent exact income, net worth, portfolio size, or debt values.
- Explain risk capacity separately from risk tolerance.
- If answers are missing, say confidence is limited.
- Keep language clear for non-finance users, but preserve useful investment terms.
- recommendationGuidance should explain how future stock recommendations should be adjusted for this user.
"""


class OnboardingAnswers(BaseModel):
    """Raw answers collected from the onboarding flow."""

    country: str = ""
    currency: str = ""
    monthly_income_band: str = Field(default="", alias="monthlyIncomeBand")
    income_predictability: str = Field(default="", alias="incomePredictability")
    monthly_disposable_band: str = Field(default="", alias="monthlyDisposableBand")
    emergency_fund_months: str = Field(default="", alias="emergencyFundMonths")
    existing_investments: list[str] = Field(default_factory=list, alias="existingInvestments")
    invested_amount_band: str = Field(default="", alias="investedAmountBand")
    debt_types: list[str] = Field(default_factory=list, alias="debtTypes")
    debt_stress: str = Field(default="", alias="debtStress")
    major_expenses: str = Field(default="", alias="majorExpenses")
    dependents: str = ""
    primary_goal: str = Field(default="", alias="primaryGoal")
    time_horizon: str = Field(default="", alias="timeHorizon")
    prior_investing: str = Field(default="", alias="priorInvesting")
    loss_reaction: str = Field(default="", alias="lossReaction")
    year_down_reaction: str = Field(default="", alias="yearDownReaction")
    crypto_comfort: str = Field(default="", alias="cryptoComfort")
    priority: str = ""
    check_frequency: str = Field(default="", alias="checkFrequency")
    volatile_stock_response: str = Field(default="", alias="volatileStockResponse")
    lifestyle_loss_capacity: str = Field(default="", alias="lifestyleLossCapacity")
    knowledge_level: str = Field(default="", alias="knowledgeLevel")
    preferred_markets: list[str] = Field(default_factory=list, alias="preferredMarkets")
    investing_style: str = Field(default="", alias="investingStyle")
    preferred_companies: list[str] = Field(default_factory=list, alias="preferredCompanies")
    restricted_sectors: list[str] = Field(default_factory=list, alias="restrictedSectors")
    conservative_default: str = Field(default="", alias="conservativeDefault")

    model_config = {"populate_by_name": True}


class OnboardingSubmitRequest(BaseModel):
    """Persisted onboarding submission."""

    answers: OnboardingAnswers


class ProfileNarrative(BaseModel):
    """LLM-written explanation of the user's derived investor profile."""

    model_config = ConfigDict(populate_by_name=True)

    headline: str
    financial_summary: str = Field(alias="financialSummary")
    risk_summary: str = Field(alias="riskSummary")
    recommendation_guidance: list[str] = Field(default_factory=list, alias="recommendationGuidance")
    cautions: list[str] = Field(default_factory=list)
    confidence_note: str = Field(alias="confidenceNote")


def build_onboarding_router(prefix: str = "/api/v1") -> APIRouter:
    """Create onboarding routes."""
    router = APIRouter(prefix=f"{prefix}/onboarding", tags=["onboarding"])

    @router.get("/profile")
    async def get_profile(user: AuthUser = Depends(require_current_user)) -> dict[str, Any]:
        """Return the current user's onboarding profile."""
        profile = db.get_onboarding_profile(user.id)
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Onboarding profile not found")
        return _public_profile(profile)

    @router.post("/profile")
    async def save_profile(
        payload: OnboardingSubmitRequest,
        user: AuthUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        """Save answers and derived financial/risk profile summaries."""
        answers = payload.answers.model_dump(mode="json", by_alias=True)
        derived = synthesize_onboarding_profile(answers)
        profile = db.save_onboarding_profile(
            user_id=user.id,
            answers=answers,
            financial_profile=derived["financialProfile"],
            risk_profile=derived["riskProfile"],
            investor_context=derived["investorContext"],
            summary=derived["summary"],
            confidence=derived["confidence"],
            missing_flags=derived["missingFlags"],
            profile_narrative=derived["profileNarrative"],
            profile_synthesis_source=derived["profileSynthesisSource"],
        )
        return _public_profile(profile)

    return router


def synthesize_onboarding_profile(answers: dict[str, Any]) -> dict[str, Any]:
    """Derive structured profiles from onboarding answers.

    Deterministic fields remain the source of truth. The LLM only enriches the
    user-facing narrative and recommendation guidance around those fields.
    """
    missing = [key for key in _required_keys() if not answers.get(key)]
    financial_score = _financial_resilience_score(answers)
    risk_capacity = _risk_capacity(answers, financial_score)
    risk_tolerance = _risk_tolerance(answers)
    confidence = "high" if len(missing) <= 2 else "medium" if len(missing) <= 7 else "low"

    financial_profile = {
        "currency": answers.get("currency", ""),
        "incomeStability": _income_stability(answers.get("incomePredictability", "")),
        "disposableIncomeBand": answers.get("monthlyDisposableBand", ""),
        "emergencyFundStrength": _emergency_strength(answers.get("emergencyFundMonths", "")),
        "debtBurden": _debt_burden(answers),
        "liquidityNeeds": _liquidity_needs(answers),
        "dependentsBurden": _dependents_burden(answers.get("dependents", "")),
        "investableCapacity": _band_from_score(risk_capacity),
        "financialResilience": _band_from_score(financial_score),
    }
    risk_profile = {
        "riskTolerance": _band_from_score(risk_tolerance),
        "riskCapacity": _band_from_score(risk_capacity),
        "lossReaction": answers.get("lossReaction", ""),
        "volatilityComfort": _volatility_comfort(answers),
        "investmentExperience": _experience_level(answers),
        "timeHorizon": answers.get("timeHorizon", ""),
        "knowledgeLevel": answers.get("knowledgeLevel", ""),
        "behaviorPattern": _behavior_pattern(answers),
    }
    investor_context = {
        "country": answers.get("country", ""),
        "currency": answers.get("currency", ""),
        "primaryGoal": answers.get("primaryGoal", ""),
        "preferredStyle": answers.get("investingStyle", ""),
        "preferredMarkets": answers.get("preferredMarkets", []),
        "preferredCompanies": answers.get("preferredCompanies", []),
        "restrictedSectors": answers.get("restrictedSectors", []),
        "conservativeDefault": answers.get("conservativeDefault", ""),
        "personalizationNotes": _personalization_notes(financial_profile, risk_profile),
    }

    base_summary = _summary(financial_profile, risk_profile, investor_context)
    narrative, source = synthesize_profile_narrative(
        answers=answers,
        financial_profile=financial_profile,
        risk_profile=risk_profile,
        investor_context=investor_context,
        confidence=confidence,
        missing_flags=missing,
        fallback_summary=base_summary,
    )

    return {
        "financialProfile": financial_profile,
        "riskProfile": risk_profile,
        "investorContext": investor_context,
        "summary": narrative["headline"],
        "confidence": confidence,
        "missingFlags": missing,
        "profileNarrative": narrative,
        "profileSynthesisSource": source,
    }


def synthesize_profile_narrative(
    *,
    answers: dict[str, Any],
    financial_profile: dict[str, Any],
    risk_profile: dict[str, Any],
    investor_context: dict[str, Any],
    confidence: str,
    missing_flags: list[str],
    fallback_summary: str,
) -> tuple[dict[str, Any], str]:
    """Generate a readable profile narrative with an LLM when configured."""
    fallback = _fallback_profile_narrative(
        fallback_summary=fallback_summary,
        financial_profile=financial_profile,
        risk_profile=risk_profile,
        investor_context=investor_context,
        confidence=confidence,
        missing_flags=missing_flags,
    )
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return fallback, "deterministic_fallback"

    payload = {
        "answers": answers,
        "financialProfile": financial_profile,
        "riskProfile": risk_profile,
        "investorContext": investor_context,
        "confidence": confidence,
        "missingFlags": missing_flags,
    }
    try:
        model = ChatOpenAI(model=PROFILE_SYNTHESIS_MODEL, temperature=0)
        structured = model.with_structured_output(ProfileNarrative)
        parsed = structured.invoke(
            [
                ("system", PROFILE_SYNTHESIS_SYSTEM),
                ("human", json.dumps(payload, indent=2, ensure_ascii=False)),
            ]
        )
        if isinstance(parsed, ProfileNarrative):
            return parsed.model_dump(mode="json", by_alias=True), "llm"
        if isinstance(parsed, dict):
            return ProfileNarrative.model_validate(parsed).model_dump(mode="json", by_alias=True), "llm"
    except Exception:
        logger.exception("LLM onboarding profile narrative failed; using deterministic fallback")
    return fallback, "deterministic_fallback"


def _fallback_profile_narrative(
    *,
    fallback_summary: str,
    financial_profile: dict[str, Any],
    risk_profile: dict[str, Any],
    investor_context: dict[str, Any],
    confidence: str,
    missing_flags: list[str],
) -> dict[str, Any]:
    return {
        "headline": fallback_summary,
        "financialSummary": (
            f"Financial resilience looks {financial_profile['financialResilience']}, with "
            f"{financial_profile['emergencyFundStrength']} emergency savings and "
            f"{financial_profile['debtBurden']} debt burden."
        ),
        "riskSummary": (
            f"Risk tolerance appears {risk_profile['riskTolerance']}, while risk capacity appears "
            f"{risk_profile['riskCapacity']}. Future recommendations should treat those separately."
        ),
        "recommendationGuidance": investor_context.get("personalizationNotes", []),
        "cautions": missing_flags[:4],
        "confidenceNote": f"Profile confidence is {confidence}.",
    }


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "answers": profile["answers"],
        "financialProfile": profile["financial_profile"],
        "riskProfile": profile["risk_profile"],
        "investorContext": profile["investor_context"],
        "summary": profile["summary"],
        "confidence": profile["confidence"],
        "missingFlags": profile["missing_flags"],
        "profileNarrative": profile["profile_narrative"],
        "profileSynthesisSource": profile["profile_synthesis_source"],
        "profileVersion": profile["profile_version"],
        "updatedAt": profile["updated_at"],
    }


def _required_keys() -> list[str]:
    return [
        "country",
        "monthlyIncomeBand",
        "incomePredictability",
        "monthlyDisposableBand",
        "emergencyFundMonths",
        "debtStress",
        "primaryGoal",
        "timeHorizon",
        "priorInvesting",
        "lossReaction",
        "yearDownReaction",
        "priority",
        "lifestyleLossCapacity",
        "knowledgeLevel",
    ]


def _financial_resilience_score(a: dict[str, Any]) -> int:
    score = 50
    score += {"none": -25, "less_than_1": -15, "1_to_3": 0, "3_to_6": 12, "6_plus": 22}.get(
        a.get("emergencyFundMonths", ""),
        0,
    )
    score += {"no_income": -25, "variable": -10, "seasonal": -12, "stable": 15, "very_stable": 20}.get(
        a.get("incomePredictability", ""),
        0,
    )
    score += {"none": -15, "tight": -8, "some": 5, "comfortable": 15, "high": 22}.get(
        a.get("monthlyDisposableBand", ""),
        0,
    )
    score += {"high_stress": -25, "manageable": -8, "none": 12}.get(a.get("debtStress", ""), 0)
    return max(0, min(100, score))


def _risk_capacity(a: dict[str, Any], financial_score: int) -> int:
    score = financial_score
    score += {"less_than_1": -20, "1_to_3": -10, "3_to_5": 8, "5_to_10": 16, "10_plus": 22}.get(
        a.get("timeHorizon", ""),
        0,
    )
    score += {"yes_major": -15, "maybe": -8, "no": 8}.get(a.get("majorExpenses", ""), 0)
    score += {"none": 6, "some": -4, "primary": -12}.get(a.get("dependents", ""), 0)
    return max(0, min(100, score))


def _risk_tolerance(a: dict[str, Any]) -> int:
    score = 50
    score += {"sell_all": -25, "sell_some": -10, "hold": 8, "buy_more": 22, "unsure": -5}.get(
        a.get("lossReaction", ""),
        0,
    )
    score += {"major_stress": -20, "uncomfortable": -8, "manageable": 8, "patient": 18}.get(
        a.get("yearDownReaction", ""),
        0,
    )
    score += {"avoid": -12, "small_only": 0, "some": 10, "active": 20}.get(a.get("cryptoComfort", ""), 0)
    score += {"avoid": -12, "watchlist": 0, "small": 8, "normal": 15, "aggressive": 25}.get(
        a.get("volatileStockResponse", ""),
        0,
    )
    score += {"avoid_losses": -16, "steady": -5, "balanced": 5, "long_term_returns": 15, "short_term": 8}.get(
        a.get("priority", ""),
        0,
    )
    return max(0, min(100, score))


def _band_from_score(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "moderate"
    return "low"


def _income_stability(value: str) -> str:
    return {"stable": "stable", "very_stable": "very stable", "variable": "variable", "seasonal": "seasonal", "no_income": "limited"}.get(value, "unknown")


def _emergency_strength(value: str) -> str:
    return {"none": "none", "less_than_1": "thin", "1_to_3": "developing", "3_to_6": "healthy", "6_plus": "strong"}.get(value, "unknown")


def _debt_burden(a: dict[str, Any]) -> str:
    if a.get("debtStress") == "high_stress":
        return "high"
    if a.get("debtStress") == "manageable" or a.get("debtTypes"):
        return "moderate"
    return "low"


def _liquidity_needs(a: dict[str, Any]) -> str:
    if a.get("majorExpenses") == "yes_major" or a.get("timeHorizon") in {"less_than_1", "1_to_3"}:
        return "high"
    if a.get("majorExpenses") == "maybe":
        return "moderate"
    return "low"


def _dependents_burden(value: str) -> str:
    return {"none": "low", "some": "moderate", "primary": "high"}.get(value, "unknown")


def _volatility_comfort(a: dict[str, Any]) -> str:
    return _band_from_score(_risk_tolerance(a))


def _experience_level(a: dict[str, Any]) -> str:
    prior = a.get("priorInvesting", "")
    if prior in {"stocks_etfs", "crypto", "advanced"}:
        return "experienced"
    if prior == "savings_only":
        return "beginner"
    return "new"


def _behavior_pattern(a: dict[str, Any]) -> str:
    if a.get("lossReaction") in {"sell_all", "sell_some"} or a.get("checkFrequency") == "daily":
        return "reactive"
    if a.get("lossReaction") == "buy_more" and a.get("timeHorizon") in {"5_to_10", "10_plus"}:
        return "opportunistic long-term"
    return "measured"


def _personalization_notes(financial: dict[str, Any], risk: dict[str, Any]) -> list[str]:
    notes = []
    if financial["financialResilience"] == "low":
        notes.append("Favor stronger balance sheets and avoid recommendations that require near-term cash flexibility.")
    if risk["riskTolerance"] == "low":
        notes.append("Prefer lower-volatility ideas and clearer downside risks.")
    if risk["riskCapacity"] == "high" and risk["riskTolerance"] in {"moderate", "high"}:
        notes.append("Longer-horizon opportunities can be considered when evidence quality is strong.")
    return notes or ["Use a balanced default until more behavior and portfolio data is available."]


def _summary(financial: dict[str, Any], risk: dict[str, Any], context: dict[str, Any]) -> str:
    return (
        f"Profile suggests {risk['riskTolerance']} risk tolerance and {risk['riskCapacity']} risk capacity, "
        f"with {financial['financialResilience']} financial resilience. Primary goal: "
        f"{context.get('primaryGoal') or 'not specified'}."
    )
