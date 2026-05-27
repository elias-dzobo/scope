"""Pydantic models for the user-facing final research memo (LLM synthesis output)."""

from __future__ import annotations

from pydantic import BaseModel, Field, ConfigDict


class PillarTakeaway(BaseModel):
    """One pillar section in the investment memo."""

    model_config = ConfigDict(populate_by_name=True)

    pillar_name: str = Field(alias="pillarName", description="Six-pillar framework name.")
    score: int = Field(ge=0, le=100, description="Pillar score from research scoring.")
    plain_english_summary: str = Field(
        alias="plainEnglishSummary",
        description="Short, non-jargon summary of what this pillar means for the investor.",
    )
    position: str = Field(
        description="Overall stance for this pillar (e.g. supportive, mixed, weak, insufficient data).",
    )
    why_it_matters: str = Field(
        alias="whyItMatters",
        description="Why this pillar matters for the investment case in plain language.",
    )
    supporting_points: list[str] = Field(
        default_factory=list,
        alias="supportingPoints",
        description="2-4 concrete supporting points grounded in evidence.",
    )
    watch_items: list[str] = Field(
        default_factory=list,
        alias="watchItems",
        description="Things to monitor or verify for this pillar.",
    )
    technical_details: str = Field(
        default="",
        alias="technicalDetails",
        description="Deeper metrics, jargon, or methodology notes for expanded view.",
    )


class InvestmentQuality(BaseModel):
    """Company-level attractiveness independent of a specific user's profile."""

    model_config = ConfigDict(populate_by_name=True)

    score: int = Field(ge=0, le=100, description="Standalone investment-quality score.")
    rating: str = Field(description="Plain-language standalone rating, such as strong, mixed, or weak.")
    rationale: list[str] = Field(default_factory=list, description="Evidence-backed reasons for the rating.")
    confidence: str = Field(description="Confidence in the standalone quality assessment.")


class InvestorFit(BaseModel):
    """How well the research fits the current investor context."""

    model_config = ConfigDict(populate_by_name=True)

    score: int = Field(ge=0, le=100, description="Suitability score for the available investor context.")
    rating: str = Field(description="Plain-language suitability rating.")
    rationale: list[str] = Field(default_factory=list, description="Reasons this may or may not fit the investor.")
    constraints: list[str] = Field(default_factory=list, description="Profile-related constraints or unknowns.")
    profile_basis: str = Field(
        alias="profileBasis",
        description="Whether this used an anonymous default profile or a real user profile.",
    )


class FinalRecommendation(BaseModel):
    """Action-oriented recommendation that combines quality and investor fit."""

    model_config = ConfigDict(populate_by_name=True)

    action: str = Field(description="Final recommendation action, such as Buy, Watchlist, Hold, or Avoid.")
    confidence: str = Field(description="Confidence in the final recommendation.")
    explanation: str = Field(description="User-facing explanation of why this action was chosen.")
    suitability_notes: list[str] = Field(
        default_factory=list,
        alias="suitabilityNotes",
        description="Notes about how personalization affected, or did not affect, the recommendation.",
    )


class PersonalizedRecommendation(BaseModel):
    """Recommendation contract that is ready for user-profile personalization."""

    model_config = ConfigDict(populate_by_name=True)

    investment_quality: InvestmentQuality = Field(alias="investmentQuality")
    investor_fit: InvestorFit = Field(alias="investorFit")
    final_recommendation: FinalRecommendation = Field(alias="finalRecommendation")


class FinalResearchSynthesis(BaseModel):
    """Structured final memo returned to the API as finalSynthesis."""

    model_config = ConfigDict(populate_by_name=True)

    company_snapshot: str = Field(
        alias="companySnapshot",
        description="What the company does, how it makes money, why investors care.",
    )
    investment_takeaway: str = Field(
        alias="investmentTakeaway",
        description="Plain-English headline recommendation summary.",
    )
    personalized_recommendation: PersonalizedRecommendation = Field(
        alias="personalizedRecommendation",
        description="Personalization-ready split between investment quality, investor fit, and final action.",
    )
    recommendation_rationale: list[str] = Field(
        default_factory=list,
        alias="recommendationRationale",
        description="3-5 user-facing reasons for the rating.",
    )
    main_risks: list[str] = Field(
        default_factory=list,
        alias="mainRisks",
        description="3-5 plain-English risks or watch items.",
    )
    pillar_takeaways: list[PillarTakeaway] = Field(
        default_factory=list,
        alias="pillarTakeaways",
        description="One entry per pillar from the framework.",
    )
    bottom_line: str = Field(
        alias="bottomLine",
        description="Short closing memo.",
    )
    source_note: str = Field(
        alias="sourceNote",
        description="Note that conclusions are tied to filings, grounded search, and parsed documents.",
    )
