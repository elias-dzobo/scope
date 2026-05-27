from pydantic import BaseModel, Field


class QueryItem(BaseModel):
    query: str = Field(
        min_length=1,
        description="Exact search query string.",
    )
    intent: str = Field(
        default="",
        description="Intent behind the query.",
    )


class PillarQueryPlan(BaseModel):
    pillar_name: str = Field(description="The pillar name.")
    objective: str = Field(
        default="",
        description="Short objective for this pillar.",
    )
    queries: list[QueryItem] = Field(
        min_length=1,
        description="Search queries for this pillar.",
    )


class QueryGenerationResponse(BaseModel):
    stock: str = Field(description="Canonical stock/company name.")
    pillars: list[PillarQueryPlan] = Field(
        min_length=1,
        description="Structured query plan across all pillars.",
    )


class EvaluationResponse(BaseModel):
    source_trust_score: int = Field(
        ge=0,
        le=100,
        description="Relevance and sufficiency score for the source.",
    )
    is_relevant: bool = Field(
        description="Whether the source is relevant for the pillar and stock.",
    )
    summary: str = Field(
        default="",
        description="One-paragraph explanation of the decision.",
    )


class BatchEvaluationItem(BaseModel):
    candidate_id: str = Field(description="Candidate identifier from the prompt.")
    source_trust_score: int = Field(ge=0, le=100, description="Relevance and sufficiency score for the source.")
    is_relevant: bool = Field(description="Whether the source is relevant for the pillar and stock.")
    summary: str = Field(default="", description="Short explanation of the decision.")


class BatchEvaluationResponse(BaseModel):
    evaluations: list[BatchEvaluationItem] = Field(default_factory=list, description="Batch relevance decisions.")


class EvidenceFact(BaseModel):
    pillar_name: str = Field(description="Pillar this evidence maps to.")
    signal_name: str = Field(description="Sub-signal identified for the pillar.")
    source_title: str = Field(default="", description="Source title.")
    metric_name: str = Field(default="", description="Named metric extracted from source.")
    metric_value: str = Field(default="", description="Metric value as surfaced in source text.")
    period: str = Field(default="", description="Time period tied to metric if available.")
    excerpt: str = Field(default="", description="Short evidence excerpt.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in extracted evidence.")


class PillarEvidenceExtractionResponse(BaseModel):
    pillar_name: str = Field(description="Pillar name for extracted evidence.")
    facts: list[EvidenceFact] = Field(default_factory=list, description="Structured evidence facts.")


class PillarAssessment(BaseModel):
    pillar_name: str = Field(description="Pillar name.")
    score: int = Field(ge=0, le=100, description="Pillar score from 0 to 100.")
    confidence: float = Field(ge=0.0, le=1.0, description="Assessment confidence.")
    strengths: list[str] = Field(default_factory=list, description="Strength highlights.")
    gaps: list[str] = Field(default_factory=list, description="Evidence gaps.")
    evidence_count: int = Field(ge=0, description="Number of evidence facts used.")
    category: str = Field(default="Insufficient Data", description="Pillar category label.")
    synopsis: str = Field(default="", description="Short synopsis of the pillar for this stock.")
    analysis: str = Field(default="", description="Longer rationale for why this pillar received its score.")


class StockScorecard(BaseModel):
    stock_name: str = Field(description="Canonical stock name.")
    ticker: str = Field(description="Ticker symbol.")
    pillar_scores: dict[str, int] = Field(description="Scores by pillar.")
    overall_score: int = Field(ge=0, le=100, description="Overall weighted score.")
    confidence: float = Field(ge=0.0, le=1.0, description="Overall confidence.")
    recommendation: str = Field(description="Recommendation band.")
    reasoning: str = Field(default="", description="Short explanation of recommendation.")
    pillar_classifications: dict[str, str] = Field(
        default_factory=dict,
        description="Classification labels by pillar (Strong/Neutral/Weak).",
    )
    valuation_status: str = Field(
        default="Unknown",
        description="Valuation state for action framing (Overvalued/Fair/Undervalued).",
    )
    technical_state: str = Field(
        default="Unknown",
        description="Technical trend state (Bullish/Neutral/Bearish).",
    )
    bullish_drivers: list[str] = Field(default_factory=list, description="Top bullish drivers.")
    key_risks: list[str] = Field(default_factory=list, description="Top risks.")
    invalidation_conditions: list[str] = Field(
        default_factory=list,
        description="Conditions that would invalidate the current recommendation.",
    )
    recommendation_confidence: str = Field(
        default="Low",
        description="Low/Medium/High confidence label for recommendation.",
    )
