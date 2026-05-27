from research_core.harness.documents import DocumentParser, classify_statement_type
from research_core.harness.grounding import parse_grounded_response
from research_core.harness.models import PrimaryDocument, ResearchWorkstream, ResearchBrief, ResearchEntity
from research_core.harness.tools import ResearchToolFacade


def test_grounded_metadata_converts_to_sources_and_evidence():
    workstream = ResearchWorkstream(
        id="valuation",
        title="Valuation",
        pillar_name="Valuation",
        goal="Assess valuation",
    )
    brief = ResearchBrief(
        objective="Research the company",
        mode="company_equity_research",
        entities=[ResearchEntity(name="Example Corp", ticker="EXM")],
        selected_pillars=["Valuation"],
    )
    payload = {
        "status": "success",
        "candidates": [
            {
                "content": {"parts": [{"text": "The company trades at a premium P/E multiple."}]},
                "groundingMetadata": {
                    "webSearchQueries": ["company valuation"],
                    "groundingChunks": [
                        {"web": {"uri": "https://example.com/valuation", "title": "Valuation source"}}
                    ],
                    "groundingSupports": [
                        {
                            "segment": {"text": "Example Corp (EXM) trades at a premium P/E multiple."},
                            "groundingChunkIndices": [0],
                        }
                    ],
                },
            }
        ],
    }

    result = parse_grounded_response(payload, workstream, brief=brief)

    assert result.status == "success"
    assert result.sources[0].title == "Valuation source"
    assert result.web_search_queries == ["company valuation"]
    assert result.evidence_candidates[0]["signal_name"] == "Multiples"


def test_document_parser_extracts_financial_statement_table():
    parser = DocumentParser()
    document = PrimaryDocument(
        document_id="doc_1",
        title="Annual report",
        url="https://example.com/report.pdf",
        document_type="pdf",
        is_primary_source=True,
    )
    text = """
    Metric    2025    2024
    Revenue   100     90
    Operating income   20   18

    Notes below.
    """

    parsed = parser.parse_document(document, html_text=text)

    assert parsed.text_chunks
    assert parsed.tables
    assert parsed.tables[0].statement_type == "income_statement"


def test_document_parser_pdf_path_uses_pypdf(monkeypatch):
    class FakePage:
        def extract_text(self):
            return "Revenue  2025\nSales  100"

    class FakeReader:
        def __init__(self, _bytes):
            self.pages = [FakePage()]

    monkeypatch.setattr("research_core.harness.documents.PdfReader", FakeReader)
    parser = DocumentParser()
    document = PrimaryDocument(document_id="doc_pdf", title="PDF", url="https://example.com/a.pdf", document_type="pdf")

    parsed = parser.parse_document(document, raw_bytes=b"%PDF fake")

    assert "Revenue" in parsed.text
    assert parsed.parser == "pypdf"


def test_statement_classifier_tags_cash_flow():
    assert classify_statement_type("Net cash provided by operating activities") == "cash_flow"


def test_document_parser_extracts_row_level_financial_facts():
    parser = DocumentParser()
    document = PrimaryDocument(
        document_id="doc_fin",
        title="Annual report",
        url="https://example.com/report.pdf",
        document_type="pdf",
        is_primary_source=True,
    )
    text = """
    Metric    2025    2024
    Revenue   $100    $90
    Operating income   $22   $18
    Free cash flow   $15   $11
    Total debt   $30   $35
    """

    parsed = parser.parse_document(document, html_text=text)
    facts = parser.extract_evidence("Financial Engine", document, parsed)

    metric_names = {fact["metric_name"] for fact in facts}
    signals = {fact["signal_name"] for fact in facts}
    assert "Revenue" in metric_names
    assert "Free cash flow" in metric_names
    assert {"Profitability", "Cash and Leverage"}.issubset(signals)
    assert any(fact["source_kind"] == "primary_document_table" for fact in facts)


def test_document_parser_extracts_guidance_and_segment_facts():
    parser = DocumentParser()
    document = PrimaryDocument(
        document_id="doc_guidance",
        title="Investor presentation",
        url="https://example.com/investor.pdf",
        document_type="pdf",
        is_primary_source=True,
    )
    text = """
    Guidance    FY2026    FY2025
    Revenue outlook   $120-$125   $100
    EPS guidance   $5.00   $4.20

    Segment Revenue    2025    2024
    Data Center segment revenue   $80   $60
    Consumer segment revenue   $20   $30
    """

    parsed = parser.parse_document(document, html_text=text)
    valuation_facts = parser.extract_evidence("Valuation", document, parsed)
    financial_facts = parser.extract_evidence("Financial Engine", document, parsed)

    assert any(table.statement_type == "guidance" for table in parsed.tables)
    assert any(table.statement_type == "segment_revenue" for table in parsed.tables)
    assert any(fact["signal_name"] == "Intrinsic Value" for fact in valuation_facts)
    assert any("segment revenue" in fact["metric_name"].lower() for fact in financial_facts)


def test_document_parser_extracts_capital_allocation_facts():
    parser = DocumentParser()
    document = PrimaryDocument(
        document_id="doc_capital",
        title="Proxy and capital allocation update",
        url="https://example.com/proxy.pdf",
        document_type="pdf",
        is_primary_source=True,
    )
    text = """
    Capital Allocation    2025    2024
    Dividends paid   $3.0   $2.8
    Share repurchases   $5.0   $4.0
    Acquisitions   $1.2   $0.5
    """

    parsed = parser.parse_document(document, html_text=text)
    facts = parser.extract_evidence("Management & Capital Allocation", document, parsed)

    metric_names = {fact["metric_name"] for fact in facts}
    assert {"Dividends paid", "Share repurchases", "Acquisitions"}.issubset(metric_names)
    assert {fact["signal_name"] for fact in facts} == {"Capital Allocation"}


def test_document_parser_uses_llm_extractor_when_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class FakeStructured:
        def invoke(self, _messages):
            return {
                "companyMatch": True,
                "documentType": "ipo_prospectus",
                "facts": [
                    {
                        "pillar_name": "Valuation",
                        "signal_name": "Intrinsic Value",
                        "source_title": "Kasapreko Prospectus",
                        "document_id": "doc_prospectus",
                        "metric_name": "Offer price",
                        "metric_value": "GHS 5.00",
                        "period": "IPO",
                        "excerpt": "The prospectus sets out the IPO offer price.",
                        "confidence": 0.86,
                    }
                ],
            }

    class FakeChat:
        def __init__(self, *args, **kwargs):
            pass

        def with_structured_output(self, _schema, method=None):
            return FakeStructured()

    monkeypatch.setattr("research_core.harness.documents.ChatOpenAI", FakeChat)
    parser = DocumentParser()
    document = PrimaryDocument(
        document_id="doc_prospectus",
        title="Kasapreko Prospectus",
        url="https://example.com/prospectus.pdf",
        document_type="pdf",
        is_primary_source=True,
    )
    parsed = parser.parse_document(document, html_text="Kasapreko IPO prospectus offer price and valuation assumptions.")

    facts = parser.extract_evidence("Valuation", document, parsed)

    assert any(fact["source_kind"] == "llm_document_extraction" for fact in facts)
    assert document.metadata["extractor_document_type"] == "ipo_prospectus"


def test_document_ranking_rejects_unrelated_sec_filings():
    facade = ResearchToolFacade()
    documents = facade._rank_document_candidates(
        "Kasapreko PLC",
        "KAS",
        [
            {
                "title": "10-K",
                "link": "https://www.sec.gov/Archives/edgar/data/21344/000002134416000050/a2015123110-k.htm",
                "snippet": "The Coca-Cola Company annual report.",
                "provider": "search",
            },
            {
                "title": "Annual Reports Ghana - Kasapreko",
                "link": "https://annualreportsghana.com/fixed_income/kasapreko/",
                "snippet": "Kasapreko PLC fixed income annual reports.",
                "provider": "search",
            },
        ],
        max_documents=5,
    )

    assert [doc.url for doc in documents] == ["https://annualreportsghana.com/fixed_income/kasapreko/"]
