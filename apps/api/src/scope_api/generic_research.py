"""Generic financial research harness.

This harness handles broad market, industry, comparison, and theme questions
that do not naturally map to a single company scorecard. It uses the existing
Gemini grounding tool path as the first research primitive and returns a small,
traceable research result that the advisor can synthesize and save to memory.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from uuid import uuid4

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from research_core.harness.models import (
    EvidenceRequirement,
    ResearchBrief,
    ResearchEntity,
    ResearchMode,
    ResearchWorkstream,
)
from research_core.harness.tools import ResearchToolFacade


GENERIC_SYNTHESIS_MODEL = os.getenv("GENERIC_RESEARCH_SYNTHESIS_MODEL", "gpt-4o-mini")
GENERIC_SYNTHESIS_SYSTEM = """You are Scope's generic financial research synthesizer.

Turn grounded research results into a complete, user-facing research memo.
Rules:
- Answer the user's actual question, not just the workstream prompt.
- Preserve financial and market detail, but explain it clearly.
- Organize broad thematic research by value-chain layer, industry segment, or mechanism.
- Separate direct beneficiaries from second-order beneficiaries when possible.
- Include risks, valuation caveats, and what would need monitoring next.
- Do not provide guarantees or direct personal financial instructions.
- Do not invent tickers, metrics, or citations not present in the supplied research.
- If the evidence is weak or mostly secondary, say so in limitations/source notes.

Return structured data matching the GenericResearchSynthesis schema."""


class GenericBeneficiary(BaseModel):
    """A company or stock mentioned as a possible beneficiary of a theme."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    ticker: str = ""
    reason: str
    evidence_strength: str = Field(default="medium", alias="evidenceStrength")


class GenericResearchLayer(BaseModel):
    """One value-chain or thematic layer in a generic research memo."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    explanation: str
    companies: list[GenericBeneficiary] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class GenericResearchSynthesis(BaseModel):
    """Structured synthesis contract for broad market or industry research."""

    model_config = ConfigDict(populate_by_name=True)

    answer: str
    key_findings: list[str] = Field(default_factory=list, alias="keyFindings")
    layers: list[GenericResearchLayer] = Field(default_factory=list)
    direct_beneficiaries: list[GenericBeneficiary] = Field(default_factory=list, alias="directBeneficiaries")
    second_order_beneficiaries: list[GenericBeneficiary] = Field(default_factory=list, alias="secondOrderBeneficiaries")
    risks: list[str] = Field(default_factory=list)
    valuation_caveats: list[str] = Field(default_factory=list, alias="valuationCaveats")
    source_notes: list[str] = Field(default_factory=list, alias="sourceNotes")
    confidence: str = "low"
    synthesis_source: str = Field(default="deterministic_fallback", alias="synthesisSource")


def _dedupe(values: list[str]) -> list[str]:
    """Return non-empty values in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        out.append(normalized)
    return out


def _trim_to_complete_sentence(text: str, *, max_chars: int = 7000) -> str:
    """Trim long generated research without leaving the memo mid-sentence."""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    window = cleaned[:max_chars].rstrip()
    cut_points = [window.rfind(marker) for marker in ("\n\n", ". ", "? ", "! ")]
    cut = max(cut_points)
    if cut > max_chars * 0.65:
        return window[: cut + 1].rstrip()
    return window.rstrip(" ,;:-*") + "."


def _compact_grounded_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep grounded result payloads rich enough for synthesis but bounded."""
    compact: list[dict[str, Any]] = []
    for result in results[:4]:
        compact.append(
            {
                "query": result.get("query"),
                "answer": _trim_to_complete_sentence(str(result.get("answer") or ""), max_chars=6000),
                "coverage": result.get("coverage", {}),
                "sources": [
                    {
                        "title": source.get("title", ""),
                        "url": source.get("url") or source.get("link") or "",
                        "sourceKind": source.get("source_kind") or source.get("sourceKind") or "",
                    }
                    for source in list(result.get("sources") or [])[:12]
                    if isinstance(source, dict)
                ],
                "evidenceCandidates": list(result.get("evidence_candidates") or result.get("evidenceCandidates") or [])[:12],
                "citationSupports": list(result.get("citation_supports") or result.get("citationSupports") or [])[:12],
            }
        )
    return compact


def _extract_beneficiaries(text: str, *, limit: int = 16) -> list[GenericBeneficiary]:
    """Extract named companies/tickers from grounded prose for fallback synthesis."""
    seen: set[str] = set()
    out: list[GenericBeneficiary] = []
    for match in re.finditer(r"\*\*([^*\n()]{2,80})(?:\s+\(([A-Z][A-Z0-9.\-]{1,8})\))?:\*\*", text):
        name = match.group(1).strip()
        ticker = (match.group(2) or "").strip()
        key = f"{name.lower()}:{ticker}"
        if key in seen:
            continue
        seen.add(key)
        start = match.end()
        end = text.find("\n", start)
        reason = text[start : end if end != -1 else start + 260].strip(" :-*")
        out.append(
            GenericBeneficiary(
                name=name,
                ticker=ticker,
                reason=_trim_to_complete_sentence(reason or f"Mentioned as strategically exposed to the theme.", max_chars=260),
                evidenceStrength="medium",
            )
        )
        if len(out) >= limit:
            break
    return out


def _extract_layers(text: str) -> list[GenericResearchLayer]:
    """Extract markdown numbered sections into fallback thematic layers."""
    pattern = re.compile(r"\*\*\d+\.\s*([^*]+?)\*\*")
    matches = list(pattern.finditer(text))
    layers: list[GenericResearchLayer] = []
    for index, match in enumerate(matches[:8]):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        layers.append(
            GenericResearchLayer(
                name=match.group(1).strip(),
                explanation=_trim_to_complete_sentence(body, max_chars=700),
                companies=_extract_beneficiaries(body, limit=8),
                risks=[],
            )
        )
    return layers


class GenericResearchSynthesizer:
    """LLM-led synthesis layer for broad market, industry, and theme research."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or GENERIC_SYNTHESIS_MODEL

    def generate(
        self,
        *,
        query: str,
        plan: dict[str, Any],
        grounded_results: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        open_questions: list[str],
        fallback_text: str,
    ) -> GenericResearchSynthesis:
        """Return a complete generic research memo, using LLM when configured."""
        fallback = self._fallback(
            query=query,
            plan=plan,
            fallback_text=fallback_text,
            sources=sources,
            open_questions=open_questions,
        )
        if not os.getenv("OPENAI_API_KEY", "").strip():
            return fallback
        payload = {
            "query": query,
            "plan": plan,
            "groundedResults": _compact_grounded_results(grounded_results),
            "sources": sources[:20],
            "openQuestions": open_questions[:12],
            "deterministicFallback": fallback.model_dump(mode="json", by_alias=True),
        }
        try:
            model = ChatOpenAI(model=self.model_name, temperature=0.1)
            structured = model.with_structured_output(GenericResearchSynthesis, method="function_calling")
            parsed = structured.invoke(
                [
                    ("system", GENERIC_SYNTHESIS_SYSTEM),
                    ("human", json.dumps(payload, ensure_ascii=False, indent=2)),
                ]
            )
            out = parsed if isinstance(parsed, GenericResearchSynthesis) else GenericResearchSynthesis.model_validate(parsed)
            if self._is_usable(out):
                out.synthesis_source = "llm"
                return out
        except Exception:
            return fallback
        return fallback

    @staticmethod
    def _fallback(
        *,
        query: str,
        plan: dict[str, Any],
        fallback_text: str,
        sources: list[dict[str, Any]],
        open_questions: list[str],
    ) -> GenericResearchSynthesis:
        """Create a structured deterministic memo when model synthesis is unavailable."""
        answer_body = _trim_to_complete_sentence(fallback_text, max_chars=7000)
        layers = _extract_layers(answer_body)
        beneficiaries = _extract_beneficiaries(answer_body)
        source_note = f"Grounded research returned {len(sources)} source(s)."
        if open_questions:
            source_note += f" Remaining gaps: {', '.join(open_questions[:3])}."
        answer = (
            f"Fresh research for: {query}\n\n"
            f"{answer_body}"
            if answer_body
            else "Fresh research did not produce enough usable synthesis to answer this question confidently."
        )
        return GenericResearchSynthesis(
            answer=answer,
            keyFindings=GenericFinancialResearchHarness._key_findings(answer_body),
            layers=layers,
            directBeneficiaries=beneficiaries[:10],
            secondOrderBeneficiaries=beneficiaries[10:16],
            risks=["This is thematic research, so company-level valuation and execution risks still need separate review."]
            if beneficiaries
            else [],
            valuationCaveats=[
                "A company can be strategically positioned for a theme and still be overvalued as a stock.",
                "Use company-specific research before treating any beneficiary list as an investable shortlist.",
            ],
            sourceNotes=[source_note],
            confidence="medium" if len(sources) >= 2 and not open_questions else "low",
            synthesisSource="deterministic_fallback",
        )

    @staticmethod
    def _is_usable(synthesis: GenericResearchSynthesis) -> bool:
        """Reject incomplete LLM outputs before they reach the advisor."""
        answer = synthesis.answer.strip()
        if len(answer) < 600:
            return False
        if re.search(r"(\*\*\s*\d+\.?|\b\d+\.|[:;,]\s*)$", answer):
            return False
        return bool(synthesis.key_findings or synthesis.layers or synthesis.direct_beneficiaries)


class GenericFinancialResearchHarness:
    """Run bounded grounded research for open-ended financial questions."""

    def __init__(self, tools: ResearchToolFacade | None = None) -> None:
        self.tools = tools or ResearchToolFacade()

    def run(
        self,
        *,
        query: str,
        plan: dict[str, Any],
        research_requests: list[dict[str, Any]],
        user_profile: dict[str, Any] | None = None,
        max_workstreams: int = 2,
    ) -> dict[str, Any]:
        """Execute grounded research workstreams for a general finance query."""
        brief = self._build_brief(query=query, plan=plan, user_profile=user_profile or {})
        workstreams = self._build_workstreams(query=query, plan=plan, research_requests=research_requests)
        results = []
        for workstream in workstreams[: max(1, max_workstreams)]:
            grounded = self.tools.run_grounded_workstream_research(brief, workstream)
            result = grounded.model_dump(mode="json")
            coverage = self._evaluate_workstream_result(result)
            if coverage["status"] == "weak":
                retry_workstream = workstream.model_copy(deep=True)
                retry_workstream.id = f"{workstream.id}_retry"
                retry_workstream.goal = f"{workstream.goal} Focus on recent, source-backed evidence for: {', '.join(coverage['gaps'])}."
                retry_grounded = self.tools.run_grounded_workstream_research(brief, retry_workstream)
                retry_result = retry_grounded.model_dump(mode="json")
                retry_coverage = self._evaluate_workstream_result(retry_result)
                if retry_coverage["score"] >= coverage["score"]:
                    result = retry_result
                    coverage = retry_coverage
                    coverage["retried"] = True
            result["coverage"] = coverage
            results.append(result)
        return self._summarize(query=query, plan=plan, workstreams=workstreams, grounded_results=results)

    def _build_brief(self, *, query: str, plan: dict[str, Any], user_profile: dict[str, Any]) -> ResearchBrief:
        """Create a generic research brief from the advisor plan."""
        entities = [ResearchEntity(type="market", name=theme) for theme in plan.get("themes", [])]
        entities.extend(ResearchEntity(type="ticker", name=ticker, ticker=ticker) for ticker in plan.get("tickers", []))
        if not entities:
            entities.append(ResearchEntity(type="other", name=query[:120]))
        return ResearchBrief(
            objective=query,
            mode=ResearchMode.OPEN_ENDED_RESEARCH,
            entities=entities,
            constraints={"advisorPlan": plan},
            user_context=user_profile,
        )

    def _build_workstreams(
        self,
        *,
        query: str,
        plan: dict[str, Any],
        research_requests: list[dict[str, Any]],
    ) -> list[ResearchWorkstream]:
        """Translate coverage gaps into generic grounded workstreams."""
        requests = research_requests or [{"reason": "generic_question", "query": query, "targets": plan.get("themes", [])}]
        workstreams: list[ResearchWorkstream] = []
        for idx, request in enumerate(requests):
            reason = str(request.get("reason") or f"gap_{idx + 1}")
            targets = [str(target) for target in request.get("targets", [])]
            workstreams.append(
                ResearchWorkstream(
                    id=f"generic_{idx + 1}_{uuid4().hex[:8]}",
                    title=f"Generic research: {reason}",
                    goal=str(request.get("query") or query),
                    required_evidence=[
                        EvidenceRequirement(
                            name="grounded evidence",
                            description="Grounded sources that directly address the financial research question.",
                            min_facts=2,
                            preferred_source_types=["grounded_search", "recent_sources", "primary_documents"],
                        )
                    ],
                    search_focus=["recent market context", "primary sources when available", *targets],
                    open_gaps=[reason],
                    metadata={"advisorRequest": request, "themes": plan.get("themes", []), "tickers": plan.get("tickers", [])},
                )
            )
        return workstreams

    @staticmethod
    def _evaluate_workstream_result(result: dict[str, Any]) -> dict[str, Any]:
        """Assess whether one generic research workstream has enough support."""
        sources = list(result.get("sources") or [])
        evidence = list(result.get("evidence_candidates") or result.get("evidenceCandidates") or [])
        answer = str(result.get("answer") or "").strip()
        supports = list(result.get("citation_supports") or result.get("citationSupports") or [])
        gaps: list[str] = []
        score = 0.0
        if answer:
            score += 0.25
        else:
            gaps.append("missing_answer")
        if len(sources) >= 2:
            score += 0.3
        else:
            gaps.append("limited_sources")
        if evidence:
            score += 0.25
        else:
            gaps.append("missing_evidence_candidates")
        if supports:
            score += 0.2
        else:
            gaps.append("missing_citation_supports")
        return {
            "status": "sufficient" if score >= 0.7 else "weak",
            "score": round(score, 3),
            "gaps": gaps,
            "sourceCount": len(sources),
            "evidenceCount": len(evidence),
            "citationSupportCount": len(supports),
            "retried": False,
        }

    def _summarize(
        self,
        *,
        query: str,
        plan: dict[str, Any],
        workstreams: list[ResearchWorkstream],
        grounded_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Produce a compact generic research result for advisor synthesis."""
        answers = [str(result.get("answer") or "").strip() for result in grounded_results if result.get("answer")]
        sources = []
        evidence = []
        web_queries = []
        open_questions = []
        for result in grounded_results:
            sources.extend(result.get("sources") or [])
            evidence.extend(result.get("evidence_candidates") or [])
            web_queries.extend(result.get("web_search_queries") or [])
            coverage = result.get("coverage") or {}
            for gap in coverage.get("gaps", []):
                open_questions.append(f"{result.get('query') or query}: {gap}")
        synthesis = "\n\n".join(answers)
        weak_results = [result for result in grounded_results if (result.get("coverage") or {}).get("status") == "weak"]
        synthesized = GenericResearchSynthesizer().generate(
            query=query,
            plan=plan,
            grounded_results=grounded_results,
            sources=sources,
            open_questions=_dedupe(open_questions),
            fallback_text=synthesis,
        )
        synthesized_payload = synthesized.model_dump(mode="json", by_alias=True)
        return {
            "status": "completed" if answers and not weak_results else "weak",
            "query": query,
            "mode": "generic_financial_research",
            "themes": plan.get("themes", []),
            "entities": plan.get("entities", []),
            "tickers": plan.get("tickers", []),
            "researchPlan": {
                "intent": plan.get("intent", "industry_research"),
                "neededContext": plan.get("neededContext", []),
                "workstreamCount": len(workstreams),
                "retryPolicy": "retry weak workstreams once with targeted query",
            },
            "workstreams": [workstream.model_dump(mode="json") for workstream in workstreams],
            "workstreamResults": grounded_results,
            "groundedResults": grounded_results,
            "rawSynthesis": synthesis,
            "synthesis": synthesized.answer,
            "keyFindings": synthesized.key_findings,
            "layers": synthesized_payload.get("layers", []),
            "directBeneficiaries": synthesized_payload.get("directBeneficiaries", []),
            "secondOrderBeneficiaries": synthesized_payload.get("secondOrderBeneficiaries", []),
            "risks": synthesized.risks,
            "valuationCaveats": synthesized.valuation_caveats,
            "sourceNotes": synthesized.source_notes,
            "synthesisSource": synthesized.synthesis_source,
            "openQuestions": _dedupe(open_questions),
            "sources": sources,
            "evidenceCandidates": evidence,
            "webSearchQueries": web_queries,
            "confidence": synthesized.confidence if not weak_results else "low",
        }

    @staticmethod
    def _key_findings(text: str, limit: int = 5) -> list[str]:
        """Extract compact findings from grounded answer paragraphs."""
        findings: list[str] = []
        for line in text.splitlines():
            stripped = line.strip().strip("* ")
            if not stripped:
                continue
            if re.match(r"^\d+\.\s+.+", stripped):
                findings.append(stripped)
                continue
            if stripped.startswith("- "):
                findings.append(stripped[2:].strip())
                continue
            for sentence in re.split(r"(?<!\d)\.(?!\d)\s+", stripped):
                normalized = sentence.strip()
                if len(normalized) < 40:
                    continue
                if not normalized.endswith((".", "?", "!")):
                    normalized = f"{normalized}."
                findings.append(normalized)
        return _dedupe(findings)[:limit]
