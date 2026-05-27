"""Quality gates for grading harness outputs before the agent moves on."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from research_core.scoring.main import PILLAR_SIGNALS
from research_core.harness.models import QualityGateResult, ResearchPlan, ResearchWorkstream, WorkstreamStatus


EVIDENCE_JUDGE_MODEL = os.getenv("EVIDENCE_JUDGE_MODEL", "gpt-4o-mini")

EVIDENCE_JUDGE_SYSTEM = """You judge whether an evidence fact can support an investment research pillar.

Check company/entity match, pillar relevance, signal relevance, source quality, stale/weak evidence, and unrelated filings.
Reject evidence from unrelated companies even if the source is high quality. Be strict but not brittle."""


class EvidenceAlignmentJudgement(BaseModel):
    """LLM judgement for one evidence item."""

    verdict: str = Field(description="accepted, rejected, or needs_review")
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    company_match: bool = Field(default=True, alias="companyMatch")
    pillar_relevance: bool = Field(default=True, alias="pillarRelevance")


class ResearchQualityGates:
    """Evaluate whether research outputs are strong enough to continue."""

    minimum_fact_confidence = 0.35
    minimum_alignment_score = 0.65

    def evaluate_company_research(self, plan: ResearchPlan, result: dict[str, Any]) -> QualityGateResult:
        """Grade a completed company research result against the plan.

        The gate checks both quantity and alignment. A fact only counts as usable
        evidence when it maps to the expected pillar signals and carries concrete
        metric or excerpt content, which keeps the harness from accepting
        superficially populated but irrelevant evidence.
        """
        evidence_by_pillar = result.get("evidence_by_pillar", {})
        sources_by_pillar = result.get("sources_by_pillar", {})
        weak_workstreams: list[str] = []
        gaps: list[str] = []
        metrics: dict[str, Any] = {
            "gateSource": "llm_assisted" if os.getenv("OPENAI_API_KEY", "").strip() else "deterministic_fallback",
            "evidence_counts": {},
            "aligned_evidence_counts": {},
            "misaligned_evidence_counts": {},
            "average_alignment_scores": {},
            "evidence_alignment_details": {},
            "source_counts": {},
            "primary_source_counts": {},
            "grounded_source_counts": {},
            "grounded_citation_support_counts": {},
            "source_freshness_details": {},
            "dated_source_counts": {},
            "stale_source_counts": {},
            "primary_document_count": 0,
            "financial_statement_table_count": 0,
        }
        grounded_by_pillar = {
            item.get("pillar_name", ""): item
            for item in result.get("grounded_results", [])
            if isinstance(item, dict)
        }
        primary_documents = [
            item for item in result.get("documents", []) if isinstance(item, dict) and item.get("is_primary_source")
        ]
        financial_tables = [
            item
            for item in result.get("document_tables", [])
            if isinstance(item, dict)
            and item.get("statement_type") in {"income_statement", "balance_sheet", "cash_flow", "segment_revenue", "guidance", "key_metrics"}
        ]
        metrics["primary_document_count"] = len(primary_documents)
        metrics["financial_statement_table_count"] = len(financial_tables)

        for workstream in plan.workstreams:
            pillar = workstream.pillar_name or workstream.title
            evidence_items = evidence_by_pillar.get(pillar, [])
            evidence_count = len(evidence_items)
            alignment_details = [
                self._score_evidence_alignment(pillar, fact, workstream)
                for fact in evidence_items
            ]
            aligned_evidence = [item for item in alignment_details if item["score"] >= self.minimum_alignment_score]
            aligned_count = len(aligned_evidence)
            misaligned_count = evidence_count - aligned_count
            sources = sources_by_pillar.get(pillar, [])
            source_count = len(sources)
            primary_source_count = sum(1 for source in sources if source.get("is_primary_source"))
            source_freshness = [self._score_source_freshness(source) for source in sources]
            dated_sources = [item for item in source_freshness if item["as_of"]]
            stale_sources = [item for item in dated_sources if item["is_stale"]]

            metrics["evidence_counts"][pillar] = evidence_count
            metrics["aligned_evidence_counts"][pillar] = aligned_count
            metrics["misaligned_evidence_counts"][pillar] = misaligned_count
            metrics["average_alignment_scores"][pillar] = round(
                sum(item["score"] for item in alignment_details) / len(alignment_details),
                3,
            ) if alignment_details else 0.0
            metrics["evidence_alignment_details"][pillar] = alignment_details[:10]
            metrics["source_counts"][pillar] = source_count
            metrics["primary_source_counts"][pillar] = primary_source_count
            metrics["source_freshness_details"][pillar] = source_freshness[:10]
            metrics["dated_source_counts"][pillar] = len(dated_sources)
            metrics["stale_source_counts"][pillar] = len(stale_sources)
            grounded = grounded_by_pillar.get(pillar, {})
            metrics["grounded_source_counts"][pillar] = len(grounded.get("sources", [])) if grounded else 0
            metrics["grounded_citation_support_counts"][pillar] = len(grounded.get("citation_supports", [])) if grounded else 0

            if grounded and grounded.get("status") == "success" and not grounded.get("citation_supports"):
                gap = f"{pillar}: grounded answer did not include citation supports"
                gaps.append(gap)
                workstream.open_gaps.append(gap)

            if aligned_count < plan.stop_conditions.min_evidence_facts_per_workstream:
                weak_workstreams.append(workstream.id)
                gap = (
                    f"{pillar}: only {aligned_count} aligned evidence facts "
                    f"out of {evidence_count} extracted facts"
                )
                gaps.append(gap)
                workstream.open_gaps.append(gap)
                workstream.status = WorkstreamStatus.NEEDS_MORE_EVIDENCE

            if pillar in {"Financial Engine", "Management & Capital Allocation", "Valuation"} and not primary_documents:
                weak_workstreams.append(workstream.id)
                gap = f"{pillar}: no primary annual report, filing, investor deck, or governance document found"
                gaps.append(gap)
                workstream.open_gaps.append(gap)
                workstream.status = WorkstreamStatus.NEEDS_MORE_EVIDENCE

            if pillar == "Financial Engine" and primary_documents and not financial_tables:
                weak_workstreams.append(workstream.id)
                gap = "Financial Engine: no financial-statement-like tables extracted from primary documents"
                gaps.append(gap)
                workstream.open_gaps.append(gap)
                workstream.status = WorkstreamStatus.NEEDS_MORE_EVIDENCE

            if misaligned_count:
                reason_counts: dict[str, int] = {}
                for item in alignment_details:
                    if item["score"] >= self.minimum_alignment_score:
                        continue
                    for reason in item.get("reasons", []):
                        reason_counts[reason] = reason_counts.get(reason, 0) + 1
                reason_text = ", ".join(f"{reason} ({count})" for reason, count in sorted(reason_counts.items()))
                gap = f"{pillar}: {misaligned_count} extracted facts were weakly aligned"
                if reason_text:
                    gap += f" - {reason_text}"
                gaps.append(gap)
                workstream.open_gaps.append(gap)

            if source_count < plan.stop_conditions.min_sources_per_workstream:
                weak_workstreams.append(workstream.id)
                gap = f"{pillar}: only {source_count} scraped sources"
                gaps.append(gap)
                workstream.open_gaps.append(gap)
                workstream.status = WorkstreamStatus.NEEDS_MORE_EVIDENCE

            if source_count and dated_sources and len(stale_sources) == source_count:
                weak_workstreams.append(workstream.id)
                threshold = self._source_stale_after_days()
                gap = f"{pillar}: all dated sources are older than {threshold} days"
                gaps.append(gap)
                workstream.open_gaps.append(gap)
                workstream.status = WorkstreamStatus.NEEDS_MORE_EVIDENCE

        if gaps:
            target = weak_workstreams[0] if weak_workstreams else ""
            return QualityGateResult(
                passed=False,
                gate_name="company_research_evidence_coverage",
                summary="Research needs another pass before the final analysis should be trusted.",
                recommended_action="retry",
                target_workstream_id=target,
                gaps=gaps,
                metrics=metrics,
            )

        return QualityGateResult(
            passed=True,
            gate_name="company_research_evidence_coverage",
            summary="Source and evidence coverage met the initial harness thresholds.",
            recommended_action="continue",
            metrics=metrics,
        )

    def _score_evidence_alignment(
        self,
        pillar: str,
        fact: dict[str, Any],
        workstream: ResearchWorkstream,
    ) -> dict[str, Any]:
        """Score whether an evidence fact supports the target workstream.

        The score is intentionally transparent. It rewards the basics that a
        usable fact must satisfy: correct pillar, valid signal, enough content,
        signal keyword match, and some overlap with the workstream goal or
        required evidence. A future LLM judge can use the same detail payload as
        input without changing the gate contract.
        """
        expected_signals = PILLAR_SIGNALS.get(pillar, {})
        signal_name = str(fact.get("signal_name", "")).strip()
        fact_pillar = str(fact.get("pillar_name", pillar)).strip()
        confidence = float(fact.get("confidence", 1.0) or 0.0)
        metric_name = str(fact.get("metric_name", "")).strip()
        metric_value = str(fact.get("metric_value", "")).strip()
        excerpt = str(fact.get("excerpt", "")).strip()
        source_title = str(fact.get("source_title", "")).strip()
        content = f"{source_title} {metric_name} {metric_value} {excerpt}".lower()

        score = 0.0
        reasons: list[str] = []

        if fact_pillar and fact_pillar != pillar:
            reasons.append("wrong_pillar")
        else:
            score += 0.18

        if signal_name in expected_signals:
            score += 0.2
        else:
            reasons.append("invalid_signal")

        if confidence >= self.minimum_fact_confidence:
            score += min(0.16, confidence * 0.16)
        else:
            reasons.append("low_confidence")

        if metric_value or len(excerpt) >= 24:
            score += 0.14
        else:
            reasons.append("thin_content")

        signal_keywords = expected_signals.get(signal_name, [])
        if signal_keywords and any(keyword in content for keyword in signal_keywords):
            score += 0.2
        elif signal_keywords:
            reasons.append("missing_signal_language")
        else:
            reasons.append("no_signal_keywords")

        goal_terms = self._important_terms(workstream.goal)
        requirement_terms = self._important_terms(
            " ".join(
                [req.name + " " + req.description for req in workstream.required_evidence]
                + list(workstream.search_focus)
            )
        )
        goal_overlap = sorted(term for term in goal_terms if term in content)
        requirement_overlap = sorted(term for term in requirement_terms if term in content)
        if goal_overlap or requirement_overlap:
            score += 0.12
        else:
            reasons.append("weak_goal_fit")

        deterministic = {
            "score": round(min(score, 1.0), 3),
            "signal_name": signal_name,
            "source_title": source_title,
            "reasons": reasons,
            "goal_term_matches": goal_overlap[:5],
            "requirement_term_matches": requirement_overlap[:5],
        }
        return self._apply_llm_alignment_judgement(
            pillar=pillar,
            fact=fact,
            workstream=workstream,
            deterministic=deterministic,
        )

    def _apply_llm_alignment_judgement(
        self,
        *,
        pillar: str,
        fact: dict[str, Any],
        workstream: ResearchWorkstream,
        deterministic: dict[str, Any],
    ) -> dict[str, Any]:
        """Revise deterministic alignment with an LLM judge when configured."""
        if not os.getenv("OPENAI_API_KEY", "").strip():
            deterministic["judge_source"] = "deterministic_fallback"
            return deterministic
        try:
            model = ChatOpenAI(model=EVIDENCE_JUDGE_MODEL, temperature=0)
            structured = model.with_structured_output(EvidenceAlignmentJudgement, method="function_calling")
            parsed = structured.invoke(
                [
                    ("system", EVIDENCE_JUDGE_SYSTEM),
                    (
                        "human",
                        json.dumps(
                            {
                                "pillar": pillar,
                                "workstream": workstream.model_dump(mode="json"),
                                "fact": fact,
                                "deterministicBaseline": deterministic,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ]
            )
            judgement = parsed if isinstance(parsed, EvidenceAlignmentJudgement) else EvidenceAlignmentJudgement.model_validate(parsed)
            out = dict(deterministic)
            out["judge_source"] = "llm"
            out["llm_verdict"] = judgement.verdict
            out["llm_reasons"] = judgement.reasons
            out["company_match"] = judgement.company_match
            out["pillar_relevance"] = judgement.pillar_relevance
            if judgement.verdict == "rejected" or not judgement.company_match or not judgement.pillar_relevance:
                out["score"] = min(out["score"], 0.2)
                out["reasons"] = sorted(set(out.get("reasons", []) + judgement.reasons + ["llm_rejected"]))
            elif judgement.verdict == "accepted":
                out["score"] = max(out["score"], judgement.score)
            else:
                out["score"] = min(out["score"], max(0.45, judgement.score))
            return out
        except Exception:
            deterministic["judge_source"] = "deterministic_fallback"
            return deterministic

    def _is_aligned_evidence_fact(self, pillar: str, fact: dict[str, Any]) -> bool:
        """Backward-compatible boolean alignment helper for callers/tests."""
        from research_core.harness.models import ResearchWorkstream

        workstream = ResearchWorkstream(id=pillar.lower().replace(" ", "_"), title=pillar, pillar_name=pillar, goal=pillar)
        return self._score_evidence_alignment(pillar, fact, workstream)["score"] >= self.minimum_alignment_score

    @staticmethod
    def _important_terms(text: str) -> set[str]:
        """Extract high-signal terms for cheap workstream fit checks."""
        stop = {
            "and",
            "are",
            "assess",
            "company",
            "evidence",
            "for",
            "from",
            "into",
            "pillar",
            "source",
            "the",
            "this",
            "with",
        }
        terms = set(re.findall(r"[a-zA-Z][a-zA-Z0-9/&.-]{3,}", text.lower()))
        return {term for term in terms if term not in stop}

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        """Parse source date metadata without failing the gate on bad input."""
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if not value:
            return None
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _source_stale_after_days() -> int:
        """Return the age threshold for dated research sources."""
        try:
            days = int(os.getenv("SCOPE_RESEARCH_SOURCE_STALE_DAYS", "365"))
        except ValueError:
            return 365
        return days if days > 0 else 365

    def _source_as_of(self, source: dict[str, Any]) -> datetime | None:
        """Find an as-of date from common source and document metadata fields."""
        metadata = dict(source.get("metadata") or {})
        for key in [
            "asOfDate",
            "as_of_date",
            "sourceDate",
            "source_date",
            "publishedAt",
            "published_at",
            "filingDate",
            "filing_date",
            "retrievedAt",
            "retrieved_at",
            "generatedAt",
        ]:
            parsed = self._parse_datetime(source.get(key) or metadata.get(key))
            if parsed:
                return parsed
        return None

    def _score_source_freshness(self, source: dict[str, Any]) -> dict[str, Any]:
        """Create transparent source freshness metadata for gate decisions."""
        as_of = self._source_as_of(source)
        threshold = self._source_stale_after_days()
        title = str(source.get("title") or source.get("link") or "Untitled source")
        if not as_of:
            return {
                "title": title,
                "as_of": None,
                "age_days": None,
                "threshold_days": threshold,
                "is_stale": False,
                "status": "unknown",
            }
        age_days = max((datetime.now(timezone.utc) - as_of).days, 0)
        is_stale = age_days > threshold
        return {
            "title": title,
            "as_of": as_of.isoformat(),
            "age_days": age_days,
            "threshold_days": threshold,
            "is_stale": is_stale,
            "status": "stale" if is_stale else "fresh",
        }
