import pytest

from research_core.harness.controller import ResearchController
from research_core.harness.gates import ResearchQualityGates
from research_core.harness.memory import HarnessMemoryStore
from research_core.harness.models import (
    ExtractedTable,
    GroundedCitationSupport,
    GroundedResearchResult,
    GroundedSource,
    ParsedDocument,
    PrimaryDocument,
)
from research_core.harness.planner import ResearchPlanner


def test_research_planner_uses_llm_draft_with_required_pillars(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class FakeStructured:
        def invoke(self, _messages):
            return {
                "instrumentStatus": "pre_ipo",
                "plannerReasoning": "Adapted valuation and technical workstreams for a pre-IPO company.",
                "workstreams": [
                    {
                        "id": "valuation",
                        "title": "Valuation",
                        "pillarName": "Valuation",
                        "goal": "Assess proposed offer valuation using prospectus assumptions rather than live trading multiples.",
                        "searchFocus": ["IPO prospectus", "offer price", "peer multiples"],
                    }
                ],
            }

    class FakeChat:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(self, _schema, method=None):
            return FakeStructured()

    monkeypatch.setattr("research_core.harness.planner.ChatOpenAI", FakeChat)
    planner = ResearchPlanner()
    brief = planner.create_company_brief("Kasapreko PLC", "KAS", selected_pillars=["Valuation", "Financial Engine"])

    plan = planner.create_company_plan(brief)

    assert plan.metadata["plannerSource"] == "llm"
    assert [workstream.pillar_name for workstream in plan.workstreams] == ["Valuation", "Financial Engine"]
    assert plan.workstreams[0].metadata["instrumentStatus"] == "pre_ipo"
    assert "prospectus" in plan.workstreams[0].goal
    assert plan.workstreams[1].metadata["planner"] == "deterministic_fallback"


class FakeTools:
    def __init__(self):
        self.fallback_calls = []

    def run_grounded_workstream_research(self, brief, workstream):
        return GroundedResearchResult(
            workstream_id=workstream.id,
            pillar_name=workstream.pillar_name,
            answer="Valuation evidence mentions P/E multiple and fair value upside.",
            web_search_queries=["Eli Lilly valuation P/E fair value"],
            sources=[GroundedSource(source_id="g1", title="Market data", url="https://example.com")],
            citation_supports=[
                GroundedCitationSupport(
                    text="The company trades at a premium P/E multiple and has fair value upside.",
                    source_ids=["g1"],
                )
            ],
            evidence_candidates=[
                {
                    "pillar_name": "Valuation",
                    "signal_name": "Multiples",
                    "source_title": "Market data",
                    "metric_name": "P/E",
                    "metric_value": "35x",
                    "excerpt": "The company trades at a premium P/E multiple versus peers.",
                    "confidence": 0.8,
                },
                {
                    "pillar_name": "Valuation",
                    "signal_name": "Intrinsic Value",
                    "source_title": "Market data",
                    "metric_name": "Fair value upside",
                    "metric_value": "12%",
                    "excerpt": "Analyst fair value work implies modest upside from current levels.",
                    "confidence": 0.75,
                },
            ],
        )

    def discover_primary_documents(self, company_name, ticker, grounded_results=None, max_documents=8):
        return [
            PrimaryDocument(
                document_id="doc_1",
                title="Annual report",
                url="https://ir.example.com/report.pdf",
                document_type="pdf",
                source_kind="investor_relations",
                is_primary_source=True,
                score=95,
            ),
            PrimaryDocument(
                document_id="doc_2",
                title="Earnings presentation",
                url="https://ir.example.com/earnings.pdf",
                document_type="pdf",
                source_kind="investor_relations",
                is_primary_source=True,
                score=90,
            ),
            PrimaryDocument(
                document_id="doc_3",
                title="Market data",
                url="https://example.com/market",
                document_type="html",
                source_kind="financial_data",
                is_primary_source=False,
                score=70,
            )
        ]

    def fetch_document(self, ticker, document):
        return document, b"fake pdf", ""

    def parse_document(self, ticker, document, raw_bytes=None, html_text=""):
        return ParsedDocument(
            document_id=document.document_id,
            title=document.title,
            url=document.url,
            document_type=document.document_type,
            text="P/E multiple fair value upside revenue cash flow",
            text_chunks=["P/E multiple fair value upside revenue cash flow"],
            tables=[
                ExtractedTable(
                    document_id=document.document_id,
                    table_id="t1",
                    title="Key Metrics",
                    columns=["Metric", "2025"],
                    rows=[["Revenue", "$1"]],
                    statement_type="key_metrics",
                    confidence=0.8,
                )
            ],
        )

    def extract_document_tables(self, parsed):
        return [table.model_dump(mode="json") for table in parsed.tables]

    def extract_document_evidence(self, pillar, document, parsed):
        return []

    def assess_pillars(self, evidence_by_pillar):
        return {
            "Valuation": {
                "pillar_name": "Valuation",
                "score": 72,
                "confidence": 0.7,
                "strengths": ["Multiples: 1 evidence hits"],
                "gaps": [],
                "evidence_count": 2,
                "category": "Neutral",
                "synopsis": "Valuation is neutral.",
                "analysis": "Valuation is supported by multiples and fair value evidence.",
            }
        }

    def build_scorecard(self, company_name, ticker, pillar_assessments, evidence_by_pillar):
        return {
            "stock_name": company_name,
            "ticker": ticker,
            "pillar_scores": {"Valuation": 72},
            "overall_score": 72,
            "confidence": 0.7,
            "recommendation": "Watch / Accumulate on Weakness",
            "reasoning": "Strongest pillar: Valuation.",
            "pillar_classifications": {"Valuation": "Neutral"},
            "valuation_status": "Fairly Valued",
            "technical_state": "Neutral Trend",
            "bullish_drivers": ["Valuation: multiples"],
            "key_risks": ["Valuation: thin data"],
            "invalidation_conditions": [],
            "recommendation_confidence": "Medium",
        }

    def run_targeted_fallback_search(self, company_name, ticker, workstream):
        self.fallback_calls.append(workstream.id)
        return {"evidence_by_pillar": {}, "sources_by_pillar": {}}

    def run_existing_company_pipeline(self, company_name, ticker, selected_pillars=None, progress_callback=None):
        return {
            "ticker": ticker,
            "stock_name": company_name,
            "query_batch_count": 1,
            "filtered_doc_count": 1,
            "scraped_doc_count": 1,
            "overall_score": 70,
            "recommendation": "Watch / Accumulate on Weakness",
            "scorecard": {
                "stock_name": company_name,
                "ticker": ticker,
                "pillar_scores": {"Valuation": 70},
                "overall_score": 70,
                "confidence": 0.65,
                "recommendation": "Watch / Accumulate on Weakness",
                "reasoning": "Fallback summary.",
                "valuation_status": "Unknown",
                "technical_state": "Unknown",
                "bullish_drivers": [],
                "key_risks": [],
                "recommendation_confidence": "Low",
            },
            "pillar_assessments": {},
            "evidence_by_pillar": {},
            "sources_by_pillar": {},
            "runtime_profile": {},
        }


@pytest.fixture(autouse=True)
def _stub_final_synthesis(monkeypatch):
    """Avoid live OpenAI calls; full contract is tested in test_final_synthesis."""

    def fake_generate(self, *args, **kwargs):
        pillar_assessments = kwargs.get("pillar_assessments") or {}
        takeaways = []
        for name, row in pillar_assessments.items():
            takeaways.append(
                {
                    "pillarName": name,
                    "score": int(row.get("score", 0)),
                    "plainEnglishSummary": "Stub summary.",
                    "position": "stub",
                    "whyItMatters": "Stub.",
                    "supportingPoints": [],
                    "watchItems": [],
                    "technicalDetails": "",
                }
            )
        if not takeaways:
            takeaways = [
                {
                    "pillarName": "Valuation",
                    "score": 72,
                    "plainEnglishSummary": "Stub.",
                    "position": "stub",
                    "whyItMatters": "Stub.",
                    "supportingPoints": [],
                    "watchItems": [],
                    "technicalDetails": "",
                }
            ]
        return {
            "companySnapshot": "Stub company snapshot.",
            "investmentTakeaway": "Stub takeaway.",
            "personalizedRecommendation": {
                "investmentQuality": {
                    "score": 72,
                    "rating": "Stub quality",
                    "rationale": ["Stub standalone quality rationale."],
                    "confidence": "Medium",
                },
                "investorFit": {
                    "score": 50,
                    "rating": "Neutral",
                    "rationale": ["No user profile was supplied in this test."],
                    "constraints": ["Risk profile unknown."],
                    "profileBasis": "anonymous_default",
                },
                "finalRecommendation": {
                    "action": "Watchlist",
                    "confidence": "Medium",
                    "explanation": "Stub recommendation explanation.",
                    "suitabilityNotes": ["General research view."],
                },
            },
            "recommendationRationale": ["Stub reason."],
            "mainRisks": ["Stub risk."],
            "pillarTakeaways": takeaways,
            "bottomLine": "Stub bottom line.",
            "sourceNote": "Stub source note.",
        }

    monkeypatch.setattr(
        "research_core.harness.runner.FinalSynthesisGenerator.generate",
        fake_generate,
    )


def test_controller_preserves_result_contract_and_persists_harness_state(tmp_path):
    controller = ResearchController(
        planner=ResearchPlanner(),
        tools=FakeTools(),
        gates=ResearchQualityGates(),
        memory=HarnessMemoryStore(artifact_root=str(tmp_path / "artifacts")),
    )

    result = controller.run_company_research("Eli Lilly", "LLY", selected_pillars=["Valuation"])

    assert result["ticker"] == "LLY"
    assert result["harness"]["latest_gate_result"]["passed"] is True
    assert result["documents"][0]["title"] == "Annual report"
    assert result["harness"]["plan"]["workstreams"][0]["title"] == "Valuation"
    assert result.get("final_synthesis")
    assert result["final_synthesis"]["companySnapshot"]
    assert (tmp_path / "artifacts" / "LLY" / "harness" / "brief.json").exists()
    assert (tmp_path / "artifacts" / "LLY" / "harness" / "plan.json").exists()
    assert (tmp_path / "artifacts" / "LLY" / "harness" / "latest_gate_result.json").exists()


def test_quality_gate_rejects_wrong_pillar_or_empty_evidence():
    planner = ResearchPlanner()
    brief = planner.create_company_brief("Eli Lilly", "LLY", selected_pillars=["Valuation"])
    plan = planner.create_plan(brief)
    result = {
        "evidence_by_pillar": {
            "Valuation": [
                {
                    "pillar_name": "Valuation",
                    "signal_name": "Capital Allocation",
                    "metric_value": "buyback",
                    "excerpt": "The company announced a buyback program.",
                    "confidence": 0.9,
                },
                {
                    "pillar_name": "Valuation",
                    "signal_name": "Multiples",
                    "excerpt": "thin",
                    "confidence": 0.8,
                },
            ]
        },
        "sources_by_pillar": {
            "Valuation": [
                {"title": "Annual report", "is_primary_source": True},
                {"title": "Earnings presentation", "is_primary_source": True},
                {"title": "Market data", "is_primary_source": False},
            ]
        },
        "documents": [
            {
                "document_id": "doc_1",
                "title": "Annual report",
                "is_primary_source": True,
                "document_type": "pdf",
            }
        ],
        "document_tables": [{"document_id": "doc_1", "statement_type": "key_metrics"}],
    }

    gate_result = ResearchQualityGates().evaluate_company_research(plan, result)

    assert gate_result.passed is False
    assert gate_result.recommended_action == "retry"
    assert gate_result.metrics["aligned_evidence_counts"]["Valuation"] == 0
    assert gate_result.metrics["misaligned_evidence_counts"]["Valuation"] == 2
    assert gate_result.metrics["average_alignment_scores"]["Valuation"] < 0.65
    reasons = {
        reason
        for detail in gate_result.metrics["evidence_alignment_details"]["Valuation"]
        for reason in detail["reasons"]
    }
    assert "invalid_signal" in reasons
    assert "thin_content" in reasons


def test_quality_gate_records_high_alignment_for_relevant_evidence():
    planner = ResearchPlanner()
    brief = planner.create_company_brief("Eli Lilly", "LLY", selected_pillars=["Valuation"])
    plan = planner.create_plan(brief)
    result = {
        "evidence_by_pillar": {
            "Valuation": [
                {
                    "pillar_name": "Valuation",
                    "signal_name": "Multiples",
                    "metric_name": "P/E",
                    "metric_value": "35x",
                    "source_title": "Market data",
                    "excerpt": "Eli Lilly trades at a premium P/E multiple versus peers.",
                    "confidence": 0.85,
                },
                {
                    "pillar_name": "Valuation",
                    "signal_name": "Intrinsic Value",
                    "metric_name": "Fair value upside",
                    "metric_value": "12%",
                    "source_title": "Analyst report",
                    "excerpt": "Fair value work implies upside based on guidance and margin assumptions.",
                    "confidence": 0.8,
                },
            ]
        },
        "sources_by_pillar": {
            "Valuation": [
                {"title": "Annual report", "is_primary_source": True},
                {"title": "Analyst report", "is_primary_source": False},
                {"title": "Market data", "is_primary_source": False},
            ]
        },
        "documents": [{"document_id": "doc_1", "title": "Annual report", "is_primary_source": True}],
        "document_tables": [{"document_id": "doc_1", "statement_type": "key_metrics"}],
    }

    gate_result = ResearchQualityGates().evaluate_company_research(plan, result)

    assert gate_result.passed is True
    assert gate_result.metrics["aligned_evidence_counts"]["Valuation"] == 2
    assert gate_result.metrics["average_alignment_scores"]["Valuation"] >= 0.65
    assert all(
        detail["score"] >= 0.65
        for detail in gate_result.metrics["evidence_alignment_details"]["Valuation"]
    )


def test_quality_gate_llm_judge_can_reject_unrelated_evidence(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class FakeStructured:
        def invoke(self, _messages):
            return {
                "verdict": "rejected",
                "score": 0.1,
                "reasons": ["unrelated_company"],
                "companyMatch": False,
                "pillarRelevance": False,
            }

    class FakeChat:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(self, _schema, method=None):
            return FakeStructured()

    monkeypatch.setattr("research_core.harness.gates.ChatOpenAI", FakeChat)
    planner = ResearchPlanner()
    brief = planner.create_company_brief("Kasapreko PLC", "KAS", selected_pillars=["Valuation"])
    plan = planner.create_plan(brief)
    result = {
        "evidence_by_pillar": {
            "Valuation": [
                {
                    "pillar_name": "Valuation",
                    "signal_name": "Multiples",
                    "metric_name": "P/E",
                    "metric_value": "20x",
                    "source_title": "Coca-Cola 10-K",
                    "excerpt": "The Coca-Cola Company trades at a multiple based on US beverage sales.",
                    "confidence": 0.9,
                }
            ]
        },
        "sources_by_pillar": {"Valuation": [{"title": "Annual report", "is_primary_source": True}, {"title": "Market data"}, {"title": "Prospectus"}]},
        "documents": [{"document_id": "doc_1", "title": "Prospectus", "is_primary_source": True}],
        "document_tables": [{"document_id": "doc_1", "statement_type": "key_metrics"}],
    }

    gate_result = ResearchQualityGates().evaluate_company_research(plan, result)

    detail = gate_result.metrics["evidence_alignment_details"]["Valuation"][0]
    assert detail["judge_source"] == "llm"
    assert detail["score"] < 0.65
    assert "llm_rejected" in detail["reasons"]


def test_quality_gate_flags_all_dated_sources_as_stale(monkeypatch):
    monkeypatch.setenv("SCOPE_RESEARCH_SOURCE_STALE_DAYS", "30")
    planner = ResearchPlanner()
    brief = planner.create_company_brief("Eli Lilly", "LLY", selected_pillars=["Valuation"])
    plan = planner.create_plan(brief)
    result = {
        "evidence_by_pillar": {
            "Valuation": [
                {
                    "pillar_name": "Valuation",
                    "signal_name": "Multiples",
                    "metric_name": "P/E",
                    "metric_value": "35x",
                    "source_title": "Old market data",
                    "excerpt": "Eli Lilly trades at a premium P/E multiple versus peers.",
                    "confidence": 0.85,
                },
                {
                    "pillar_name": "Valuation",
                    "signal_name": "Intrinsic Value",
                    "metric_name": "Fair value upside",
                    "metric_value": "12%",
                    "source_title": "Old annual report",
                    "excerpt": "Fair value work implies upside based on guidance and margin assumptions.",
                    "confidence": 0.8,
                },
            ]
        },
        "sources_by_pillar": {
            "Valuation": [
                {
                    "title": "Old annual report",
                    "is_primary_source": True,
                    "sourceDate": "2024-01-01T00:00:00+00:00",
                },
                {
                    "title": "Old market data",
                    "is_primary_source": False,
                    "publishedAt": "2024-01-01T00:00:00+00:00",
                },
                {
                    "title": "Old analyst report",
                    "is_primary_source": False,
                    "metadata": {"retrievedAt": "2024-01-01T00:00:00+00:00"},
                },
            ]
        },
        "documents": [{"document_id": "doc_1", "title": "Old annual report", "is_primary_source": True}],
        "document_tables": [{"document_id": "doc_1", "statement_type": "key_metrics"}],
    }

    gate_result = ResearchQualityGates().evaluate_company_research(plan, result)

    assert gate_result.passed is False
    assert gate_result.metrics["dated_source_counts"]["Valuation"] == 3
    assert gate_result.metrics["stale_source_counts"]["Valuation"] == 3
    assert any("all dated sources" in gap for gap in gate_result.gaps)
