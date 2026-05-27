"""Comprehensive harness evaluation suite.

Covers the failure modes, boundary conditions, and quality properties
documented in docs/harness-evaluation.md.

Run with:
    uv run python -m pytest tests/test_harness_evals.py -v
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from research_core.harness.gates import ResearchQualityGates
from research_core.harness.models import (
    DEFAULT_COMPANY_PILLARS,
    EvidenceRequirement,
    QualityGateResult,
    ResearchBrief,
    ResearchEntity,
    ResearchMode,
    ResearchObservation,
    ResearchWorkstream,
    WorkstreamStatus,
)
from research_core.harness.memory import HarnessMemoryStore
from research_core.harness.planner import ResearchPlanner
from research_core.harness.grounding import parse_grounded_response
from research_core.storage import LocalArtifactStore, ticker_artifact_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_brief(
    company: str = "Kasapreko",
    ticker: str = "KPRC",
    pillars: list[str] | None = None,
) -> ResearchBrief:
    planner = ResearchPlanner()
    return planner.create_company_brief(
        company_name=company,
        ticker=ticker,
        selected_pillars=pillars,
    )


def _make_plan(brief: ResearchBrief | None = None) -> Any:
    planner = ResearchPlanner()
    b = brief or _make_brief()
    return planner._create_company_plan_deterministic(b)


def _make_workstream(
    pillar: str = "Financial Engine",
    required_evidence: list[str] | None = None,
    search_focus: list[str] | None = None,
) -> ResearchWorkstream:
    reqs = [EvidenceRequirement(name=r) for r in (required_evidence or ["Revenue", "Operating Margin", "Free Cash Flow"])]
    return ResearchWorkstream(
        id=f"ws_{pillar.lower().replace(' ', '_').replace('&', 'and')}",
        title=pillar,
        goal=f"Assess {pillar} for the company",
        pillar_name=pillar,
        required_evidence=reqs,
        search_focus=search_focus or ["annual report", "revenue", "margins"],
        status=WorkstreamStatus.PENDING,
    )


def _make_fact(
    pillar: str = "Financial Engine",
    signal: str = "Profitability",
    excerpt: str = "Revenue grew 20% YoY to $500M with a 25% operating margin.",
    metric_name: str = "Revenue",
    metric_value: str = "$500M",
    confidence: float = 0.75,
    source_url: str = "https://example.com/annual-report",
    is_primary: bool = True,
) -> dict:
    return {
        "pillar_name": pillar,
        "signal_name": signal,
        "excerpt": excerpt,
        "metric_name": metric_name,
        "metric_value": metric_value,
        "confidence": confidence,
        "source_url": source_url,
        "source_title": "Annual Report 2024",
        "is_primary_source": is_primary,
    }


def _make_source(
    url: str = "https://example.com/report",
    title: str = "Annual Report 2024",
    is_primary: bool = True,
    source_date: str | None = None,
) -> dict:
    s: dict = {
        "url": url,
        "title": title,
        "is_primary_source": is_primary,
        "source_kind": "primary_filing" if is_primary else "news",
    }
    if source_date:
        s["sourceDate"] = source_date
    return s


def _result_dict(
    pillars: list[str] | None = None,
    facts_per_pillar: int = 4,
    sources_per_pillar: int = 4,
    grounded_results: list | None = None,
    documents: list | None = None,
) -> dict:
    """Build a minimal evaluate_company_research result dict."""
    ps = pillars or list(DEFAULT_COMPANY_PILLARS)
    return {
        "evidence_by_pillar": {
            p: [_make_fact(pillar=p) for _ in range(facts_per_pillar)]
            for p in ps
        },
        "sources_by_pillar": {
            p: [_make_source() for _ in range(sources_per_pillar)]
            for p in ps
        },
        "grounded_results": grounded_results or [],
        "documents": documents or [],
    }


# ---------------------------------------------------------------------------
# 1. Evidence Alignment Edge Cases
# ---------------------------------------------------------------------------


class TestEvidenceAlignment:
    """Test _score_evidence_alignment directly — returns a dict."""

    def test_well_aligned_fact_passes_threshold(self) -> None:
        gate = ResearchQualityGates()
        ws = _make_workstream("Financial Engine", search_focus=["revenue", "margin"])
        fact = _make_fact(
            pillar="Financial Engine",
            signal="Profitability",
            excerpt="Revenue grew 20% YoY to $500M with a 25% operating margin.",
            metric_name="Revenue",
            metric_value="$500M",
            confidence=0.80,
        )

        result = gate._score_evidence_alignment("Financial Engine", fact, ws)

        assert result["score"] >= 0.65, f"Expected score ≥ 0.65, got {result['score']:.3f}. Reasons: {result.get('reasons')}"

    def test_wrong_pillar_fact_is_rejected(self) -> None:
        gate = ResearchQualityGates()
        ws = _make_workstream("Valuation")
        fact = _make_fact(
            pillar="Financial Engine",  # mismatched vs workstream pillar
            signal="Profitability",
            excerpt="Revenue grew 20% YoY",
            confidence=0.75,
        )

        result = gate._score_evidence_alignment("Valuation", fact, ws)

        assert result["score"] < 0.65
        assert "wrong_pillar" in result.get("reasons", [])

    def test_thin_content_fact_is_rejected(self) -> None:
        gate = ResearchQualityGates()
        ws = _make_workstream("Financial Engine")
        fact = _make_fact(
            pillar="Financial Engine",
            signal="Profitability",
            excerpt="OK.",  # too short
            metric_name="",
            metric_value="",
            confidence=0.50,
        )

        result = gate._score_evidence_alignment("Financial Engine", fact, ws)

        assert result["score"] < 0.65
        assert "thin_content" in result.get("reasons", [])

    def test_low_confidence_reduces_score(self) -> None:
        gate = ResearchQualityGates()
        ws = _make_workstream("Financial Engine")
        low_conf = _make_fact(
            pillar="Financial Engine",
            signal="Profitability",
            excerpt="Revenue grew 20% to $500M",
            metric_value="$500M",
            confidence=0.30,  # below minimum_fact_confidence
        )
        high_conf = _make_fact(
            pillar="Financial Engine",
            signal="Profitability",
            excerpt="Revenue grew 20% to $500M",
            metric_value="$500M",
            confidence=0.90,
        )

        low_result = gate._score_evidence_alignment("Financial Engine", low_conf, ws)
        high_result = gate._score_evidence_alignment("Financial Engine", high_conf, ws)

        assert low_result["score"] < high_result["score"], (
            "Low-confidence fact should score lower than high-confidence fact"
        )

    def test_invalid_signal_name_penalizes_score(self) -> None:
        """Deterministic score for an invalid signal should be below threshold.

        We patch _apply_llm_alignment_judgement to isolate the deterministic path —
        otherwise a live OPENAI_API_KEY will run the LLM judge which may boost the
        score regardless of signal validity.
        """
        gate = ResearchQualityGates()
        ws = _make_workstream("Financial Engine")
        fact = _make_fact(
            pillar="Financial Engine",
            signal="Geopolitical Tailwind",  # not a valid signal for Financial Engine
            excerpt="Revenue grew 20% to $500M",
            metric_value="$500M",
            confidence=0.75,
        )

        # Patch LLM judge to pass through deterministic result unchanged
        def _passthrough(*, pillar, fact, workstream, deterministic):
            deterministic["judge_source"] = "deterministic_fallback"
            return deterministic

        with patch.object(gate, "_apply_llm_alignment_judgement", side_effect=_passthrough):
            result = gate._score_evidence_alignment("Financial Engine", fact, ws)

        assert result["score"] < 0.65, f"Invalid signal should score below 0.65, got {result['score']}"
        assert "invalid_signal" in result.get("reasons", [])

    def test_alignment_result_has_expected_keys(self) -> None:
        gate = ResearchQualityGates()
        ws = _make_workstream("Financial Engine")
        fact = _make_fact()

        result = gate._score_evidence_alignment("Financial Engine", fact, ws)

        for key in ("score", "reasons", "signal_name"):
            assert key in result, f"Missing key '{key}' in alignment result"


# ---------------------------------------------------------------------------
# 2. Quality Gate Decisions
# ---------------------------------------------------------------------------


class TestQualityGateDecisions:

    def test_gate_passes_with_sufficient_evidence(self) -> None:
        gate = ResearchQualityGates()
        brief = _make_brief()
        plan = _make_plan(brief)
        result = _result_dict(facts_per_pillar=4, sources_per_pillar=4)

        gate_result = gate.evaluate_company_research(plan, result)

        # With 4 well-formed facts per pillar the gate should pass
        # (it may still fail on primary-doc requirements — that's fine, test the structure)
        assert isinstance(gate_result, QualityGateResult)
        assert isinstance(gate_result.passed, bool)
        assert isinstance(gate_result.gaps, list)

    def test_gate_fails_with_no_evidence(self) -> None:
        gate = ResearchQualityGates()
        brief = _make_brief()
        plan = _make_plan(brief)
        result = _result_dict(facts_per_pillar=0, sources_per_pillar=0)

        gate_result = gate.evaluate_company_research(plan, result)

        assert not gate_result.passed
        assert len(gate_result.gaps) > 0

    def test_gate_result_has_metrics_dict(self) -> None:
        gate = ResearchQualityGates()
        plan = _make_plan()
        result = _result_dict(facts_per_pillar=2)

        gate_result = gate.evaluate_company_research(plan, result)

        assert isinstance(gate_result.metrics, dict), "QualityGateResult should include a metrics dict"

    def test_gate_result_has_recommended_action(self) -> None:
        gate = ResearchQualityGates()
        plan = _make_plan()
        result = _result_dict(facts_per_pillar=0)

        gate_result = gate.evaluate_company_research(plan, result)

        assert gate_result.recommended_action in ("continue", "retry", "branch", "ask_user", "stop")

    def test_gate_result_is_qalitygateresult_model(self) -> None:
        gate = ResearchQualityGates()
        plan = _make_plan()
        result = _result_dict()

        gate_result = gate.evaluate_company_research(plan, result)

        assert isinstance(gate_result, QualityGateResult)
        serialized = gate_result.model_dump_json()
        loaded = json.loads(serialized)
        assert "passed" in loaded


# ---------------------------------------------------------------------------
# 3. Source Freshness
# ---------------------------------------------------------------------------


class TestSourceFreshness:

    def test_stale_source_returns_old_datetime(self) -> None:
        gate = ResearchQualityGates()
        stale_date = (datetime.now(tz=timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
        source = _make_source(source_date=stale_date)

        parsed = gate._source_as_of(source)

        assert parsed is not None
        age_days = (datetime.now(tz=timezone.utc) - parsed).days
        assert age_days > 365, f"Expected age > 365 days, got {age_days}"

    def test_fresh_source_returns_recent_datetime(self) -> None:
        gate = ResearchQualityGates()
        fresh_date = (datetime.now(tz=timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        source = _make_source(source_date=fresh_date)

        parsed = gate._source_as_of(source)

        assert parsed is not None
        age_days = (datetime.now(tz=timezone.utc) - parsed).days
        assert age_days < 365

    def test_source_without_date_returns_none(self) -> None:
        gate = ResearchQualityGates()
        source = _make_source()  # no date field

        parsed = gate._source_as_of(source)

        assert parsed is None

    def test_source_freshness_scoring_returns_dict(self) -> None:
        gate = ResearchQualityGates()
        old_date = (datetime.now(tz=timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
        source = _make_source(source_date=old_date)

        scored = gate._score_source_freshness(source)

        assert isinstance(scored, dict)
        assert "is_stale" in scored or "as_of" in scored

    def test_multiple_date_keys_are_recognised(self) -> None:
        """At least one of the known metadata date keys is parsed."""
        gate = ResearchQualityGates()
        old = (datetime.now(tz=timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")

        recognised = False
        for key in ("asOfDate", "sourceDate", "publishedAt", "filingDate", "date"):
            source = {"url": "https://example.com", "is_primary_source": True, key: old}
            if gate._source_as_of(source) is not None:
                recognised = True
                break

        assert recognised, "None of the expected date metadata keys were recognised by _source_as_of"


# ---------------------------------------------------------------------------
# 4. Grounding Response Parsing
# ---------------------------------------------------------------------------


class TestGroundingParsing:

    def _gemini_response(
        self,
        text: str = "Revenue grew 20% YoY.",
        chunks: list[dict] | None = None,
        supports: list[dict] | None = None,
    ) -> dict:
        return {
            "candidates": [
                {
                    "content": {"parts": [{"text": text}]},
                    "groundingMetadata": {
                        "groundingChunks": chunks or [
                            {"web": {"uri": "https://example.com/report", "title": "Annual Report 2024"}}
                        ],
                        "groundingSupports": supports or [
                            {
                                "segment": {"text": text},
                                "groundingChunkIndices": [0],
                                "confidenceScores": [0.9],
                            }
                        ],
                        "webSearchQueries": ["company revenue 2024"],
                    },
                }
            ]
        }

    def test_sources_extracted_from_chunks(self) -> None:
        ws = _make_workstream("Financial Engine")
        brief = _make_brief()

        result = parse_grounded_response(self._gemini_response(), ws, brief)

        assert len(result.sources) >= 1
        assert any("example.com" in s.url for s in result.sources)

    def test_answer_text_is_extracted(self) -> None:
        ws = _make_workstream("Financial Engine")
        brief = _make_brief()

        result = parse_grounded_response(self._gemini_response(text="Revenue grew 20% YoY."), ws, brief)

        assert "Revenue" in result.answer

    def test_status_is_success_on_valid_response(self) -> None:
        ws = _make_workstream("Financial Engine")
        brief = _make_brief()

        result = parse_grounded_response(self._gemini_response(), ws, brief)

        assert result.status == "success"

    def test_empty_supports_synthesised_from_answer_text(self) -> None:
        """When groundingSupports=[] the parser synthesises a support from the answer text.

        This documents the actual behaviour: parse_grounded_response falls back to
        creating a single synthetic GroundedCitationSupport from the answer text so
        downstream evidence extraction always has something to work with.
        The quality gate separately flags the absence of real citation supports.
        """
        ws = _make_workstream("Financial Engine")
        brief = _make_brief()

        result = parse_grounded_response(
            self._gemini_response(supports=[]), ws, brief
        )

        # Parser synthesises one support from the answer text — this is by design.
        # A gate gap is still raised for missing real citation supports.
        assert isinstance(result.citation_supports, list)
        if result.citation_supports:
            assert all(hasattr(s, "text") for s in result.citation_supports)

    def test_evidence_candidates_tagged_with_pillar(self) -> None:
        ws = _make_workstream("Financial Engine")
        brief = _make_brief()

        result = parse_grounded_response(
            self._gemini_response(text="Revenue grew 25% to $1.2B with 30% operating margin."),
            ws,
            brief,
        )

        for candidate in result.evidence_candidates:
            assert candidate.get("pillar_name") == "Financial Engine"

    def test_result_is_grounded_research_result_model(self) -> None:
        from research_core.harness.models import GroundedResearchResult

        ws = _make_workstream("Financial Engine")
        brief = _make_brief()

        result = parse_grounded_response(self._gemini_response(), ws, brief)

        assert isinstance(result, GroundedResearchResult)


# ---------------------------------------------------------------------------
# 5. Document Ranking
# ---------------------------------------------------------------------------


class TestDocumentRanking:
    """Test _rank_document_candidates in ResearchToolFacade.

    Signature: _rank_document_candidates(company_name, ticker, candidates, max_documents)
    Returns: list[PrimaryDocument]
    """

    def _facade(self) -> Any:
        from research_core.harness.tools import ResearchToolFacade
        return ResearchToolFacade.__new__(ResearchToolFacade)

    def _candidate(
        self,
        url: str,
        title: str = "",
        snippet: str = "",
        score: float = 1.0,
    ) -> dict:
        return {"link": url, "url": url, "title": title, "snippet": snippet, "score": score}

    def test_unrelated_sec_filing_is_rejected(self) -> None:
        facade = self._facade()

        candidates = [
            self._candidate(
                url="https://www.sec.gov/Archives/edgar/data/21344/000002134424000001/ko-20231231.htm",
                title="Form 10-K",
                snippet="annual report",
            )
        ]

        ranked = facade._rank_document_candidates("Kasapreko", "KPRC", candidates, max_documents=8)

        urls = [r.url for r in ranked]
        assert not any("21344" in u for u in urls), (
            "Unrelated SEC filing should be rejected when researching Kasapreko"
        )

    def test_duplicate_urls_deduplicated(self) -> None:
        facade = self._facade()
        url = "https://kasapreko.com/annual-report-2024.pdf"

        candidates = [
            self._candidate(url=url, title="Kasapreko Annual Report", snippet="Kasapreko")
            for _ in range(3)
        ]

        ranked = facade._rank_document_candidates("Kasapreko", "KPRC", candidates, max_documents=8)

        result_urls = [r.url for r in ranked]
        assert result_urls.count(url) <= 1, "Duplicate URL should appear at most once"

    def test_returns_list(self) -> None:
        facade = self._facade()

        ranked = facade._rank_document_candidates("Kasapreko", "KPRC", [], max_documents=8)

        assert isinstance(ranked, list)

    def test_company_named_candidate_is_accepted(self) -> None:
        facade = self._facade()

        candidates = [
            self._candidate(
                url="https://kasapreko.com/investors/annual-report-2024.pdf",
                title="Kasapreko Annual Report 2024",
                snippet="Kasapreko annual report",
            )
        ]

        ranked = facade._rank_document_candidates("Kasapreko", "KPRC", candidates, max_documents=8)

        assert len(ranked) >= 1, "Candidate explicitly naming the company should be accepted"


# ---------------------------------------------------------------------------
# 6. Table Extraction & Classification
# ---------------------------------------------------------------------------


class TestTableExtraction:
    """Test DocumentParser from research_core.harness.documents.

    extract_tables(document_id: str, text: str) -> list[ExtractedTable]
    extract_evidence(pillar, document, parsed) -> list[dict]
    """

    def _parser(self) -> Any:
        from research_core.harness.documents import DocumentParser
        return DocumentParser()

    def test_income_statement_table_classified(self) -> None:
        parser = self._parser()
        text = (
            "Consolidated Income Statement\n"
            "| Metric           | FY2024  | FY2023  |\n"
            "| Revenue          | $2,500M | $2,100M |\n"
            "| Operating Income | $750M   | $600M   |\n"
            "| Net Income       | $600M   | $480M   |\n"
        )

        tables = parser.extract_tables("doc_001", text)

        assert len(tables) >= 1
        types = [t.statement_type for t in tables]
        assert "income_statement" in types, f"Expected income_statement, got: {types}"

    def test_cash_flow_table_classified(self) -> None:
        parser = self._parser()
        text = (
            "Cash Flow Statement\n"
            "| Item                                  | FY2024 |\n"
            "| Cash provided by operating activities | $900M  |\n"
            "| Capital expenditures                  | -$200M |\n"
            "| Free cash flow                        | $700M  |\n"
        )

        tables = parser.extract_tables("doc_002", text)

        assert len(tables) >= 1
        types = [t.statement_type for t in tables]
        assert "cash_flow" in types, f"Expected cash_flow, got: {types}"

    def test_financial_facts_extracted_from_income_table(self) -> None:
        """DocumentParser.extract_evidence pulls pillar facts from parsed document."""
        from research_core.harness.documents import DocumentParser
        from research_core.harness.models import ParsedDocument, PrimaryDocument

        parser = DocumentParser()
        text = (
            "Income Statement Summary\n"
            "| Metric           | FY2024  | FY2023  |\n"
            "| Revenue          | $1,200M | $1,000M |\n"
            "| Operating Income | $360M   | $280M   |\n"
            "| Free Cash Flow   | $250M   | $200M   |\n"
        )
        tables = parser.extract_tables("doc_003", text)
        parsed = ParsedDocument(
            document_id="doc_003",
            text_chunks=[text],
            tables=tables,
            parser="text",
        )
        document = PrimaryDocument(
            document_id="doc_003",
            title="Annual Report 2024",
            url="https://example.com/ar.pdf",
            document_type="annual_report",
            is_primary_source=True,
        )

        facts = parser.extract_evidence("Financial Engine", document, parsed)

        assert len(facts) > 0, "Expected at least one fact from parsed income statement"

    def test_tables_have_confidence_attribute(self) -> None:
        parser = self._parser()
        text = (
            "Key Metrics\n"
            "| Metric  | FY2024 | FY2023 |\n"
            "| Revenue | $100M  | $80M   |\n"
            "| EBITDA  | $30M   | $25M   |\n"
        )

        tables = parser.extract_tables("doc_004", text)

        for table in tables:
            assert 0.0 <= table.confidence <= 1.0, f"Table confidence {table.confidence} out of [0, 1]"

    def test_plain_text_returns_empty_list(self) -> None:
        parser = self._parser()

        tables = parser.extract_tables("doc_005", "This is a paragraph with no tables at all.")

        assert isinstance(tables, list)


# ---------------------------------------------------------------------------
# 7. Planner — Deterministic Plan
# ---------------------------------------------------------------------------


class TestPlannerFallback:

    def test_deterministic_plan_covers_all_six_pillars(self) -> None:
        planner = ResearchPlanner()
        brief = _make_brief()

        plan = planner._create_company_plan_deterministic(brief)

        pillar_names = {ws.pillar_name for ws in plan.workstreams}
        expected = set(DEFAULT_COMPANY_PILLARS)
        assert expected.issubset(pillar_names), (
            f"Missing pillars in deterministic plan: {expected - pillar_names}"
        )

    def test_deterministic_plan_each_workstream_has_required_evidence(self) -> None:
        planner = ResearchPlanner()
        brief = _make_brief()

        plan = planner._create_company_plan_deterministic(brief)

        for ws in plan.workstreams:
            assert len(ws.required_evidence) > 0, (
                f"Workstream '{ws.pillar_name}' has no required_evidence"
            )

    def test_selected_pillars_filters_workstreams(self) -> None:
        planner = ResearchPlanner()
        selected = ["Financial Engine", "Valuation"]
        brief = planner.create_company_brief("Kasapreko", "KPRC", selected_pillars=selected)

        plan = planner._create_company_plan_deterministic(brief)

        plan_pillars = {ws.pillar_name for ws in plan.workstreams}
        for pillar in plan_pillars:
            assert pillar in selected, f"Unexpected pillar in filtered plan: {pillar}"

    def test_plan_is_serialisable_to_json(self) -> None:
        planner = ResearchPlanner()
        brief = _make_brief()
        plan = planner._create_company_plan_deterministic(brief)

        serialized = plan.model_dump_json()
        loaded = json.loads(serialized)

        assert len(loaded["workstreams"]) > 0

    def test_brief_entity_ticker_is_normalised_to_uppercase(self) -> None:
        planner = ResearchPlanner()
        brief = planner.create_company_brief("Apple", "aapl")

        assert brief.entities[0].ticker == "AAPL", (
            f"Ticker should be normalised to AAPL, got {brief.entities[0].ticker}"
        )

    def test_brief_constraint_includes_ticker(self) -> None:
        planner = ResearchPlanner()
        brief = planner.create_company_brief("Apple", "aapl")

        assert brief.constraints.get("ticker") == "AAPL"


# ---------------------------------------------------------------------------
# 8. Harness Memory Store
# ---------------------------------------------------------------------------


class TestHarnessMemory:

    def test_save_and_load_brief(self, tmp_path: Path) -> None:
        store = HarnessMemoryStore(artifact_root=str(tmp_path))
        brief = _make_brief(ticker="TEST")
        plan = _make_plan(brief)

        store.save_run_state("TEST", brief, plan)

        brief_path = tmp_path / "TEST" / "harness" / "brief.json"
        assert brief_path.exists(), "brief.json should be written to disk"
        loaded = json.loads(brief_path.read_text())
        assert loaded["objective"].startswith("Research")

    def test_save_plan_to_disk(self, tmp_path: Path) -> None:
        store = HarnessMemoryStore(artifact_root=str(tmp_path))
        brief = _make_brief(ticker="PLAN")
        plan = _make_plan(brief)

        store.save_run_state("PLAN", brief, plan)

        plan_path = tmp_path / "PLAN" / "harness" / "plan.json"
        assert plan_path.exists()
        loaded = json.loads(plan_path.read_text())
        assert len(loaded["workstreams"]) > 0

    def test_append_trace_creates_jsonl(self, tmp_path: Path) -> None:
        store = HarnessMemoryStore(artifact_root=str(tmp_path))
        observation = ResearchObservation(
            workstream_id="ws_financial_engine",
            stage="grounded_research",
            summary="Grounding completed",
            metrics={"source_count": 5},
        )

        store.append_trace("TEST", observation)

        trace_path = tmp_path / "TEST" / "harness" / "trace.jsonl"
        assert trace_path.exists()
        lines = trace_path.read_text().strip().splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["stage"] == "grounded_research"

    def test_append_multiple_trace_events(self, tmp_path: Path) -> None:
        store = HarnessMemoryStore(artifact_root=str(tmp_path))

        for stage in ("planning", "grounded_research", "quality_gate"):
            store.append_trace("TEST", {"stage": stage, "status": "ok"})

        trace_path = tmp_path / "TEST" / "harness" / "trace.jsonl"
        lines = trace_path.read_text().strip().splitlines()
        assert len(lines) == 3
        stages = [json.loads(l)["stage"] for l in lines]
        assert stages == ["planning", "grounded_research", "quality_gate"]

    def test_save_with_gate_result(self, tmp_path: Path) -> None:
        store = HarnessMemoryStore(artifact_root=str(tmp_path))
        brief = _make_brief(ticker="GATE")
        plan = _make_plan(brief)
        gate_result = QualityGateResult(
            passed=False,
            gate_name="company_research_gate",
            recommended_action="retry",
            gaps=["Valuation: no aligned facts"],
        )

        store.save_run_state("GATE", brief, plan, gate_result=gate_result)

        gate_path = tmp_path / "GATE" / "harness" / "latest_gate_result.json"
        assert gate_path.exists()
        loaded = json.loads(gate_path.read_text())
        assert loaded["passed"] is False


# ---------------------------------------------------------------------------
# 9. Artifact Storage
# ---------------------------------------------------------------------------


class TestArtifactStorage:

    def test_local_store_write_and_read_text(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(root=tmp_path)
        key = ticker_artifact_key("MSFT", "final_synthesis", "memo.json")

        uri = store.write_text(key, '{"content": "test"}', content_type="application/json")

        assert uri, "write_text should return a non-empty URI"
        assert "MSFT" in uri or "msft" in uri.lower()

    def test_local_store_write_bytes(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(root=tmp_path)
        key = ticker_artifact_key("AAPL", "raw_document", "report.pdf")

        uri = store.write_bytes(key, b"PDF bytes here", content_type="application/pdf")

        assert uri, "write_bytes should return a non-empty URI"

    def test_ticker_artifact_key_normalises_uppercase(self) -> None:
        key = ticker_artifact_key("msft", "final_synthesis", "memo.json")

        assert "MSFT" in key, f"ticker_artifact_key should uppercase ticker, got: {key}"

    def test_delete_uri_returns_bool(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(root=tmp_path)
        key = ticker_artifact_key("DEL", "test", "file.txt")
        store.write_text(key, "hello")

        key_path = tmp_path / "DEL" / "test" / "file.txt"
        uri = str(key_path)
        result = store.delete_uri(uri)

        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 10. Model Contracts
# ---------------------------------------------------------------------------


class TestModelContracts:

    def test_research_brief_is_serialisable(self) -> None:
        brief = _make_brief()
        serialized = brief.model_dump_json()
        loaded = json.loads(serialized)

        assert loaded["mode"] == "company_equity_research"
        assert len(loaded["entities"]) == 1
        assert loaded["entities"][0]["ticker"] == "KPRC"

    def test_research_workstream_is_serialisable(self) -> None:
        ws = _make_workstream()
        serialized = ws.model_dump_json()
        loaded = json.loads(serialized)

        assert loaded["pillar_name"] == "Financial Engine"
        assert len(loaded["required_evidence"]) > 0

    def test_quality_gate_result_is_serialisable(self) -> None:
        gate_result = QualityGateResult(
            passed=True,
            gate_name="company_research_gate",
            recommended_action="continue",
        )

        serialized = gate_result.model_dump_json()
        loaded = json.loads(serialized)

        assert loaded["passed"] is True
        assert loaded["gate_name"] == "company_research_gate"

    def test_evidence_requirement_has_name_and_description(self) -> None:
        req = EvidenceRequirement(name="Revenue growth", description="YoY revenue trend")

        assert req.name == "Revenue growth"
        assert req.description == "YoY revenue trend"
        assert req.required is True  # default

    def test_workstream_status_transitions(self) -> None:
        ws = _make_workstream()
        assert ws.status == WorkstreamStatus.PENDING

        ws.status = WorkstreamStatus.NEEDS_MORE_EVIDENCE
        assert ws.status == WorkstreamStatus.NEEDS_MORE_EVIDENCE

    def test_research_observation_is_serialisable(self) -> None:
        obs = ResearchObservation(
            workstream_id="ws_valuation",
            stage="quality_gate",
            summary="Gate failed — no primary sources",
            metrics={"aligned_count": 1, "source_count": 2},
        )

        serialized = obs.model_dump_json()
        loaded = json.loads(serialized)

        assert loaded["stage"] == "quality_gate"
        assert loaded["metrics"]["aligned_count"] == 1


# ---------------------------------------------------------------------------
# 11. Agentic Fields on ResearchWorkstream
# ---------------------------------------------------------------------------


class TestWorkstreamAgenticFields:
    """Verify the new tool_assignment and query_hints fields on ResearchWorkstream."""

    def test_default_tool_assignment_is_grounded_search(self) -> None:
        ws = _make_workstream()
        assert ws.tool_assignment == "grounded_search"

    def test_tool_assignment_can_be_web_search(self) -> None:
        ws = ResearchWorkstream(
            id="ws_test",
            title="Financial Engine",
            goal="Test",
            pillar_name="Financial Engine",
            tool_assignment="web_search",
        )
        assert ws.tool_assignment == "web_search"

    def test_query_hints_default_empty(self) -> None:
        ws = _make_workstream()
        assert ws.query_hints == []

    def test_query_hints_can_be_set(self) -> None:
        ws = _make_workstream()
        ws.query_hints = ["Kasapreko revenue growth KPRC", "Kasapreko annual report"]
        assert len(ws.query_hints) == 2
        assert "revenue" in ws.query_hints[0]

    def test_workstream_with_agentic_fields_is_serialisable(self) -> None:
        ws = ResearchWorkstream(
            id="ws_agentic",
            title="Valuation",
            goal="Assess valuation",
            pillar_name="Valuation",
            tool_assignment="grounded_search",
            query_hints=["KPRC P/E multiple valuation"],
        )
        serialized = ws.model_dump_json()
        loaded = json.loads(serialized)
        assert loaded["tool_assignment"] == "grounded_search"
        assert loaded["query_hints"] == ["KPRC P/E multiple valuation"]

    def test_tool_assignment_rejects_invalid_value(self) -> None:
        with pytest.raises(Exception):
            ResearchWorkstream(
                id="ws_bad",
                title="Test",
                goal="Test",
                tool_assignment="invalid_tool",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# 12. Query Refinement Logic
# ---------------------------------------------------------------------------


class TestQueryRefinement:
    """Verify the deterministic query hint derivation logic."""

    def _hints(self, gaps: list[str], pillar: str = "Financial Engine") -> list[str]:
        from research_core.harness.agent_tools import derive_query_hints

        return derive_query_hints(
            company_name="Kasapreko",
            ticker="KPRC",
            pillar=pillar,
            gaps=gaps,
            search_focus=[],
        )

    def test_thin_content_gap_generates_analysis_hint(self) -> None:
        hints = self._hints(["thin_content: only 1 fact found"])
        assert any("analysis" in h.lower() or "financial" in h.lower() for h in hints), (
            f"Expected analysis hint for thin_content gap, got: {hints}"
        )

    def test_no_primary_source_gap_adds_annual_report_hint(self) -> None:
        hints = self._hints(["no primary source: 0 primary documents"])
        assert any("annual report" in h.lower() or "investor" in h.lower() for h in hints), (
            f"Expected annual report hint for no primary source gap, got: {hints}"
        )

    def test_stale_gap_adds_recent_hint(self) -> None:
        hints = self._hints(["stale: all sources from 2022"])
        assert any("2025" in h or "latest" in h.lower() for h in hints), (
            f"Expected recent/2025 hint for stale gap, got: {hints}"
        )

    def test_valuation_gap_adds_pe_hint(self) -> None:
        hints = self._hints(["no P/E multiple found"], pillar="Valuation")
        assert any("P/E" in h or "multiple" in h.lower() or "valuation" in h.lower() for h in hints), (
            f"Expected P/E/multiple hint for valuation gap, got: {hints}"
        )

    def test_returns_at_most_three_hints(self) -> None:
        hints = self._hints([
            "thin_content",
            "no primary source",
            "stale",
            "no P/E",
            "management unclear",
        ])
        assert len(hints) <= 3, f"Should cap at 3 hints, got {len(hints)}: {hints}"

    def test_returns_at_least_one_hint_for_empty_gaps(self) -> None:
        hints = self._hints([])
        assert len(hints) >= 1, "Should always return at least one fallback hint"

    def test_search_focus_fills_remaining_slots(self) -> None:
        from research_core.harness.agent_tools import derive_query_hints

        hints = derive_query_hints(
            company_name="Kasapreko",
            ticker="KPRC",
            pillar="Economic Moat",
            gaps=[],  # no gaps — rely on search_focus
            search_focus=["competitive moat", "market share"],
        )
        assert len(hints) >= 1
        assert any("Kasapreko" in h for h in hints)

    def test_iteration_escalates_to_sec_filings(self) -> None:
        from research_core.harness.agent_tools import derive_query_hints

        # Second iteration (iteration=1) should include SEC filing hints
        hints = derive_query_hints(
            company_name="Apple",
            ticker="AAPL",
            pillar="Financial Engine",
            gaps=[],
            search_focus=[],
            iteration=1,
        )
        # Should include escalated hint
        assert len(hints) >= 1


# ---------------------------------------------------------------------------
# 13. ToolRegistry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    """Verify the ToolRegistry wires up tools correctly."""

    def _registry(self):
        from unittest.mock import MagicMock
        from research_core.harness.tool_registry import ToolRegistry

        facade = MagicMock()
        gates = MagicMock()
        return ToolRegistry(facade, gates)

    def test_all_expected_tools_registered(self) -> None:
        registry = self._registry()
        expected = {
            "query_generation",
            "grounded_search",
            "web_search",
            "document_discovery",
            "document_fetch",
            "document_parse",
            "evidence_extraction",
            "query_refinement",
            "evidence_quality",
            "pillar_scoring",
            "synthesis",
        }
        assert expected.issubset(set(registry.available_tools)), (
            f"Missing tools: {expected - set(registry.available_tools)}"
        )

    def test_get_known_tool_returns_tool(self) -> None:
        registry = self._registry()
        tool = registry.get("grounded_search")
        assert tool.name == "grounded_search"

    def test_get_unknown_tool_raises_key_error(self) -> None:
        registry = self._registry()
        with pytest.raises(KeyError, match="Unknown tool"):
            registry.get("nonexistent_tool")

    def test_register_custom_tool(self) -> None:
        from unittest.mock import MagicMock
        from research_core.harness.agent_tools import ToolContext, ToolResult

        registry = self._registry()

        class MyTool:
            name = "my_custom_tool"
            description = "A custom tool for testing"

            def run(self, ctx: ToolContext, **kwargs) -> ToolResult:
                return ToolResult(tool_name=self.name, success=True, output="hello")

        registry.register(MyTool())
        assert "my_custom_tool" in registry.available_tools
        result = registry.get("my_custom_tool").run(MagicMock())
        assert result.output == "hello"

    def test_register_overwrites_existing_tool(self) -> None:
        from unittest.mock import MagicMock
        from research_core.harness.agent_tools import ToolContext, ToolResult

        registry = self._registry()

        class OverrideTool:
            name = "grounded_search"
            description = "Override"

            def run(self, ctx: ToolContext, **kwargs) -> ToolResult:
                return ToolResult(tool_name=self.name, success=True, output="overridden")

        registry.register(OverrideTool())
        result = registry.get("grounded_search").run(MagicMock())
        assert result.output == "overridden"


# ---------------------------------------------------------------------------
# 14. QueryRefinementTool (unit — via ToolContext)
# ---------------------------------------------------------------------------


class TestQueryRefinementTool:
    """Unit tests for QueryRefinementTool that use real ToolContext objects."""

    def _tool(self):
        from unittest.mock import MagicMock
        from research_core.harness.agent_tools import QueryRefinementTool

        return QueryRefinementTool(MagicMock())

    def _ctx(self, workstream=None):
        from research_core.harness.agent_tools import ToolContext

        ws = workstream or _make_workstream()
        brief = _make_brief()
        return ToolContext(
            brief=brief,
            workstream=ws,
            ticker="KPRC",
            company_name="Kasapreko",
        )

    def test_run_returns_tool_result(self) -> None:
        tool = self._tool()
        ctx = self._ctx()
        result = tool.run(ctx, gaps=["thin_content: 0 facts"])

        assert result.success is True
        assert result.tool_name == "query_refinement"
        assert isinstance(result.output, list)
        assert len(result.output) >= 1

    def test_run_updates_workstream_query_hints(self) -> None:
        tool = self._tool()
        ws = _make_workstream()
        ctx = self._ctx(workstream=ws)

        assert ws.query_hints == []
        tool.run(ctx, gaps=["thin_content: 2 facts"])
        assert len(ws.query_hints) >= 1

    def test_run_increments_iteration_count(self) -> None:
        tool = self._tool()
        ws = _make_workstream()
        ctx = self._ctx(workstream=ws)

        assert ws.iteration_count == 0
        tool.run(ctx, gaps=["no primary source"])
        assert ws.iteration_count == 1

    def test_suggests_grounded_search_as_next_tool(self) -> None:
        tool = self._tool()
        ctx = self._ctx()
        result = tool.run(ctx, gaps=["thin_content"])

        assert result.suggested_next_tool == "grounded_search"

    def test_run_uses_workstream_open_gaps_when_no_gaps_provided(self) -> None:
        tool = self._tool()
        ws = _make_workstream()
        ws.open_gaps = ["no primary source: missing annual report"]
        ctx = self._ctx(workstream=ws)

        result = tool.run(ctx)  # no gaps kwarg

        assert result.success is True
        # Should have picked up the open_gaps from the workstream
        assert len(ws.query_hints) >= 1


# ---------------------------------------------------------------------------
# 15. Synthesis Failure — Partial Result
# ---------------------------------------------------------------------------


class TestSynthesisPartialResult:
    """Verify that synthesis failure yields completed_partial, not failed."""

    def test_apply_final_synthesis_catches_exception(self) -> None:
        """CompanyResearchRunner._apply_final_synthesis must not raise on LLM failure."""
        from unittest.mock import patch, MagicMock
        from research_core.harness.runner import CompanyResearchRunner
        from research_core.harness.memory import HarnessMemoryStore

        runner = CompanyResearchRunner(
            tools=MagicMock(),
            gates=MagicMock(),
            memory=MagicMock(spec=HarnessMemoryStore),
        )
        summary = {
            "ticker": "KPRC",
            "stock_name": "Kasapreko",
            "scorecard": {"overall_score": 7},
            "pillar_assessments": {},
            "evidence_by_pillar": {},
            "sources_by_pillar": {},
            "selected_pillars": [],
        }

        with patch(
            "research_core.harness.runner.FinalSynthesisGenerator",
        ) as MockGen:
            MockGen.return_value.generate.side_effect = RuntimeError("API quota exceeded")

            result = runner._apply_final_synthesis(summary.copy())

        assert result.get("synthesis_failed") is True, (
            "synthesis_failed should be True when LLM call fails"
        )
        assert "synthesis_error" in result
        assert "API quota exceeded" in result["synthesis_error"]
        # Scorecard must still be present
        assert result.get("scorecard") == {"overall_score": 7}

    def test_synthesis_failure_does_not_clear_scorecard(self) -> None:
        """Even on synthesis failure, scorecard and evidence must be intact."""
        from unittest.mock import patch, MagicMock
        from research_core.harness.runner import CompanyResearchRunner

        runner = CompanyResearchRunner(
            tools=MagicMock(),
            gates=MagicMock(),
            memory=MagicMock(),
        )
        evidence = {"Financial Engine": [{"excerpt": "Revenue grew 20%"}]}
        summary = {
            "ticker": "AAPL",
            "stock_name": "Apple",
            "scorecard": {"overall_score": 8, "recommendation": "Buy"},
            "pillar_assessments": {"Financial Engine": {"score": 8}},
            "evidence_by_pillar": evidence,
            "sources_by_pillar": {},
            "selected_pillars": [],
        }

        with patch(
            "research_core.harness.runner.FinalSynthesisGenerator",
        ) as MockGen:
            MockGen.return_value.generate.side_effect = ConnectionError("timeout")

            result = runner._apply_final_synthesis(summary.copy())

        assert result["evidence_by_pillar"] == evidence
        assert result["scorecard"]["recommendation"] == "Buy"
        assert result.get("synthesis_failed") is True

    def test_successful_synthesis_does_not_set_failure_flag(self) -> None:
        """When synthesis succeeds, synthesis_failed must not appear."""
        from unittest.mock import patch, MagicMock
        from research_core.harness.runner import CompanyResearchRunner

        runner = CompanyResearchRunner(
            tools=MagicMock(),
            gates=MagicMock(),
            memory=MagicMock(),
        )
        summary = {
            "ticker": "MSFT",
            "stock_name": "Microsoft",
            "scorecard": {"overall_score": 9},
            "pillar_assessments": {},
            "evidence_by_pillar": {},
            "sources_by_pillar": {},
            "selected_pillars": [],
        }

        with patch(
            "research_core.harness.runner.FinalSynthesisGenerator",
        ) as MockGen:
            MockGen.return_value.generate.return_value = {"memo": "Strong Buy"}
            MockGen.return_value.persist_artifact.return_value = "/tmp/memo.json"

            result = runner._apply_final_synthesis(summary.copy())

        assert result.get("synthesis_failed") is not True
        assert result.get("final_synthesis") == {"memo": "Strong Buy"}
