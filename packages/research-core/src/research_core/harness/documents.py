"""Primary-document acquisition and parsing utilities."""

from __future__ import annotations

import hashlib
import json
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from research_core.scoring.main import PILLAR_SIGNALS
from research_core.harness.models import (
    DocumentEvidenceFact,
    ExtractedTable,
    ParsedDocument,
    PrimaryDocument,
)
from provider_integrations.tools.main import scrape_site_detailed
from research_core.storage import ArtifactStore, LocalArtifactStore, ticker_artifact_key

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional in minimal envs
    PdfReader = None


CORE_DOCUMENT_PILLARS = {"Financial Engine", "Management & Capital Allocation", "Valuation"}
DOCUMENT_EXTRACTOR_MODEL = os.getenv("DOCUMENT_EXTRACTOR_MODEL", "gpt-4o-mini")

DOCUMENT_EXTRACTOR_SYSTEM = """Extract investment research evidence from a parsed company document.

Only extract facts that appear to belong to the requested company and pillar.
Reject unrelated filings or generic boilerplate. Prefer concrete metrics, periods, tables, guidance, governance facts, valuation inputs, and risk disclosures.
Return concise facts with confidence."""


class DocumentExtractionResult(BaseModel):
    """Structured facts extracted by the document intelligence layer."""

    company_match: bool = Field(default=True, alias="companyMatch")
    document_type: str = Field(default="unknown", alias="documentType")
    facts: list[DocumentEvidenceFact] = Field(default_factory=list)

DOCUMENT_QUERIES: tuple[tuple[str, str], ...] = (
    ("annual_report", '"{company}" {ticker} annual report filetype:pdf'),
    ("quarterly_results", '"{company}" {ticker} quarterly results earnings presentation filetype:pdf'),
    ("investor_presentation", '"{company}" {ticker} investor presentation filetype:pdf'),
    ("proxy_governance", '"{company}" {ticker} proxy statement governance filetype:pdf'),
    ("regulatory_filing", '"{company}" {ticker} 10-K OR 20-F OR annual report site:sec.gov'),
    ("transcript", '"{company}" {ticker} earnings call transcript'),
)


class DocumentParser:
    """Parse primary documents into text chunks, tables, and evidence facts."""

    def parse_document(self, document: PrimaryDocument, raw_bytes: bytes | None = None, html_text: str = "") -> ParsedDocument:
        """Parse one fetched document into normalized text and table records."""
        if document.document_type == "pdf" and raw_bytes:
            text = self._extract_pdf_text(raw_bytes)
            parser = "pypdf"
        else:
            text = html_text
            parser = "html_text"

        chunks = self._chunk_text(text)
        tables = self.extract_tables(document.document_id, text)
        return ParsedDocument(
            document_id=document.document_id,
            title=document.title,
            url=document.url,
            document_type=document.document_type,
            text=text[:80000],
            text_chunks=chunks,
            tables=tables,
            parser=parser,
            metadata={"character_count": len(text)},
        )

    def extract_tables(self, document_id: str, text: str) -> list[ExtractedTable]:
        """Extract simple text tables using conservative row heuristics."""
        tables: list[ExtractedTable] = []
        lines = [line.strip() for line in text.splitlines()]
        current: list[list[str]] = []
        for line in lines:
            columns = self._split_table_line(line)
            if len(columns) >= 2:
                current.append(columns)
                continue
            if len(current) >= 3:
                tables.append(self._build_table(document_id, len(tables) + 1, current))
            current = []
        if len(current) >= 3:
            tables.append(self._build_table(document_id, len(tables) + 1, current))
        return [table for table in tables if table.confidence >= 0.35]

    def extract_evidence(self, pillar: str, document: PrimaryDocument, parsed: ParsedDocument) -> list[dict[str, Any]]:
        """Extract deterministic pillar evidence from parsed document text and tables."""
        signal_map = PILLAR_SIGNALS.get(pillar, {})
        facts: list[dict[str, Any]] = []
        for chunk in parsed.text_chunks:
            chunk_l = chunk.lower()
            for signal_name, keywords in signal_map.items():
                if not any(keyword in chunk_l for keyword in keywords):
                    continue
                facts.append(
                    DocumentEvidenceFact(
                        pillar_name=pillar,
                        signal_name=signal_name,
                        source_title=document.title,
                        document_id=document.document_id,
                        excerpt=chunk[:350],
                        confidence=0.7 if document.is_primary_source else 0.55,
                    ).model_dump()
                )
        for table in parsed.tables:
            facts.extend(self._extract_table_row_facts(pillar, document, table))
            for signal_name in self._signals_for_table(pillar, table):
                facts.append(
                    DocumentEvidenceFact(
                        pillar_name=pillar,
                        signal_name=signal_name,
                        source_title=document.title,
                        document_id=document.document_id,
                        metric_name=table.statement_type,
                        metric_value=f"{len(table.rows)} rows",
                        excerpt=f"{table.title or table.statement_type}: {', '.join(table.columns[:4])}",
                        confidence=max(table.confidence, 0.65),
                    ).model_dump()
                )
        facts.extend(self._extract_evidence_llm(pillar, document, parsed))
        return facts

    def _extract_evidence_llm(self, pillar: str, document: PrimaryDocument, parsed: ParsedDocument) -> list[dict[str, Any]]:
        """Use an LLM document extractor when configured, with deterministic fallback."""
        if not os.getenv("OPENAI_API_KEY", "").strip() or not parsed.text.strip():
            return []
        try:
            model = ChatOpenAI(model=DOCUMENT_EXTRACTOR_MODEL, temperature=0)
            structured = model.with_structured_output(DocumentExtractionResult, method="function_calling")
            parsed_result = structured.invoke(
                [
                    ("system", DOCUMENT_EXTRACTOR_SYSTEM),
                    (
                        "human",
                        json.dumps(
                            {
                                "pillar": pillar,
                                "document": document.model_dump(mode="json"),
                                "allowedSignals": list(PILLAR_SIGNALS.get(pillar, {})),
                                "text": parsed.text[:16000],
                                "tables": [table.model_dump(mode="json") for table in parsed.tables[:8]],
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ]
            )
            result = parsed_result if isinstance(parsed_result, DocumentExtractionResult) else DocumentExtractionResult.model_validate(parsed_result)
            document.metadata["extractor_source"] = "llm"
            document.metadata["extractor_company_match"] = result.company_match
            document.metadata["extractor_document_type"] = result.document_type
            if not result.company_match:
                return []
            out: list[dict[str, Any]] = []
            for fact in result.facts[:24]:
                payload = fact.model_dump()
                payload["pillar_name"] = pillar
                payload["source_title"] = payload.get("source_title") or document.title
                payload["document_id"] = payload.get("document_id") or document.document_id
                payload["source_kind"] = "llm_document_extraction"
                out.append(payload)
            return out
        except Exception:
            document.metadata["extractor_source"] = "deterministic_fallback"
            return []

    def _extract_pdf_text(self, raw_bytes: bytes) -> str:
        if PdfReader is None:
            return ""
        reader = PdfReader(BytesIO(raw_bytes))
        chunks: list[str] = []
        for page in reader.pages[:80]:
            page_text = (page.extract_text() or "").strip()
            if page_text:
                chunks.append(page_text)
            if sum(len(chunk) for chunk in chunks) >= 120000:
                break
        return "\n".join(chunks)

    def _chunk_text(self, text: str, chunk_size: int = 1800) -> list[str]:
        clean = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not clean:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(clean):
            chunks.append(clean[start : start + chunk_size])
            start += chunk_size
        return chunks

    def _split_table_line(self, line: str) -> list[str]:
        if "|" in line:
            columns = [part.strip() for part in line.split("|") if part.strip()]
        elif "\t" in line:
            columns = [part.strip() for part in line.split("\t") if part.strip()]
        else:
            columns = [part.strip() for part in re.split(r"\s{2,}", line) if part.strip()]
        numeric_cols = sum(bool(re.search(r"[-$()]?\d", column)) for column in columns)
        if len(columns) >= 2 and (numeric_cols or any(keyword in line.lower() for keyword in FINANCIAL_TABLE_KEYWORDS)):
            return columns
        return []

    def _build_table(self, document_id: str, index: int, rows: list[list[str]]) -> ExtractedTable:
        normalized_rows = self._normalize_table_rows(rows)
        columns = normalized_rows[0]
        body = normalized_rows[1:]
        statement_type = classify_statement_type(" ".join(" ".join(row) for row in rows[:8]))
        confidence = 0.35 + min(0.3, len(body) / 20)
        if self._has_period_columns(columns):
            confidence += 0.15
        if statement_type != "unknown":
            confidence += 0.2
        return ExtractedTable(
            document_id=document_id,
            table_id=f"{document_id}_table_{index}",
            title=statement_type.replace("_", " ").title() if statement_type != "unknown" else "",
            columns=columns,
            rows=body[:80],
            statement_type=statement_type,
            confidence=min(1.0, confidence),
        )

    def _normalize_table_rows(self, rows: list[list[str]]) -> list[list[str]]:
        """Pad and trim rows so parsed tables have stable rectangular shape."""
        if not rows:
            return []
        width = max(len(row) for row in rows)
        normalized: list[list[str]] = []
        for row in rows:
            cells = [cell.strip() for cell in row]
            if len(cells) < width:
                cells.extend([""] * (width - len(cells)))
            normalized.append(cells[:width])
        return normalized

    def _has_period_columns(self, columns: list[str]) -> bool:
        """Return whether a table has year/quarter style period columns."""
        joined = " ".join(columns).lower()
        return bool(re.search(r"\b20\d{2}\b|\b19\d{2}\b|\bq[1-4]\b|fy\s?\d{2,4}", joined))

    def _signals_for_table(self, pillar: str, table: ExtractedTable) -> list[str]:
        if pillar == "Financial Engine" and table.statement_type in {"income_statement", "cash_flow", "balance_sheet", "key_metrics", "segment_revenue"}:
            return ["Profitability"] if table.statement_type != "cash_flow" else ["Cash and Leverage"]
        if pillar == "Valuation" and table.statement_type in {"guidance", "key_metrics"}:
            return ["Multiples", "Intrinsic Value"]
        if pillar == "Management & Capital Allocation" and table.statement_type in {"cash_flow", "key_metrics"}:
            return ["Capital Allocation"]
        return []

    def _extract_table_row_facts(
        self,
        pillar: str,
        document: PrimaryDocument,
        table: ExtractedTable,
    ) -> list[dict[str, Any]]:
        """Extract concrete metric facts from rows in parsed tables."""
        facts: list[dict[str, Any]] = []
        for row in table.rows[:80]:
            if not row:
                continue
            label = row[0].strip()
            values = [cell.strip() for cell in row[1:] if cell.strip()]
            metric_value = values[0] if values else ""
            if not label or not metric_value:
                continue
            signal = self._signal_for_metric_label(pillar, table.statement_type, label)
            if not signal:
                continue
            period = self._period_for_row(table.columns, row)
            facts.append(
                DocumentEvidenceFact(
                    pillar_name=pillar,
                    signal_name=signal,
                    source_title=document.title,
                    document_id=document.document_id,
                    metric_name=label,
                    metric_value=metric_value,
                    period=period,
                    excerpt=self._row_excerpt(table, row),
                    confidence=max(0.68, table.confidence),
                    source_kind="primary_document_table",
                ).model_dump()
            )
        return facts

    def _signal_for_metric_label(self, pillar: str, statement_type: str, label: str) -> str:
        """Map a table row label to a pillar signal."""
        lowered = label.lower()
        if pillar == "Financial Engine":
            if any(term in lowered for term in FINANCIAL_PROFITABILITY_TERMS):
                return "Profitability"
            if any(term in lowered for term in FINANCIAL_CASH_LEVERAGE_TERMS):
                return "Cash and Leverage"
            if statement_type == "segment_revenue" and any(term in lowered for term in {"revenue", "sales", "segment"}):
                return "Profitability"
        if pillar == "Valuation":
            if any(term in lowered for term in VALUATION_MULTIPLE_TERMS):
                return "Multiples"
            if statement_type == "guidance" or any(term in lowered for term in VALUATION_INTRINSIC_TERMS):
                return "Intrinsic Value"
        if pillar == "Management & Capital Allocation":
            if any(term in lowered for term in CAPITAL_ALLOCATION_TERMS):
                return "Capital Allocation"
            if any(term in lowered for term in GOVERNANCE_TERMS):
                return "Governance"
        if pillar == "Macro & Industry":
            if any(term in lowered for term in {"market", "demand", "volume", "industry", "tam"}):
                return "Secular Trends"
            if any(term in lowered for term in {"share", "competitor", "competition"}):
                return "Market Structure"
        return ""

    def _period_for_row(self, columns: list[str], row: list[str]) -> str:
        """Pick the period associated with the first non-empty value cell."""
        for idx, cell in enumerate(row[1:], start=1):
            if cell.strip() and idx < len(columns):
                return columns[idx]
        return ""

    def _row_excerpt(self, table: ExtractedTable, row: list[str]) -> str:
        """Build a compact audit-friendly excerpt for one table row."""
        pairs = []
        for idx, cell in enumerate(row[1:], start=1):
            if not cell.strip():
                continue
            label = table.columns[idx] if idx < len(table.columns) else f"col_{idx}"
            pairs.append(f"{label}: {cell}")
        joined = "; ".join(pairs[:4])
        prefix = table.title or table.statement_type.replace("_", " ")
        return f"{prefix} table row '{row[0]}': {joined}"[:350]


FINANCIAL_TABLE_KEYWORDS = {
    "revenue",
    "sales",
    "income",
    "profit",
    "cash flow",
    "assets",
    "liabilities",
    "eps",
    "earnings per share",
    "guidance",
    "outlook",
    "forecast",
    "segment",
    "dividend",
    "buyback",
    "repurchase",
    "acquisition",
}

FINANCIAL_PROFITABILITY_TERMS = {
    "revenue",
    "sales",
    "gross profit",
    "operating income",
    "operating margin",
    "net income",
    "eps",
    "earnings per share",
    "ebitda",
    "margin",
    "profit",
}

FINANCIAL_CASH_LEVERAGE_TERMS = {
    "cash flow",
    "free cash flow",
    "operating cash",
    "cash provided",
    "debt",
    "borrowings",
    "liabilities",
    "leverage",
    "cash and cash equivalents",
    "liquidity",
}

VALUATION_MULTIPLE_TERMS = {
    "p/e",
    "pe ratio",
    "ev/ebitda",
    "multiple",
    "enterprise value",
    "price target",
}

VALUATION_INTRINSIC_TERMS = {
    "guidance",
    "outlook",
    "forecast",
    "fair value",
    "upside",
    "downside",
    "dcf",
    "margin assumption",
}

CAPITAL_ALLOCATION_TERMS = {
    "dividend",
    "buyback",
    "repurchase",
    "share repurchase",
    "acquisition",
    "capex",
    "capital expenditure",
    "reinvestment",
}

GOVERNANCE_TERMS = {
    "board",
    "director",
    "insider",
    "ownership",
    "governance",
    "compensation",
}


def classify_statement_type(text: str) -> str:
    """Classify a text table into a financial-statement family."""
    lowered = text.lower()
    if any(term in lowered for term in ("guidance", "outlook", "forecast", "expected", "target")):
        return "guidance"
    if any(term in lowered for term in ("segment", "geographic", "product revenue", "business unit")):
        return "segment_revenue"
    if any(term in lowered for term in ("dividend", "buyback", "repurchase", "acquisition", "capital allocation")):
        return "key_metrics"
    if any(term in lowered for term in ("cash flow", "operating cash", "free cash", "operating activities")):
        return "cash_flow"
    if any(term in lowered for term in ("assets", "liabilities", "equity")):
        return "balance_sheet"
    if any(term in lowered for term in ("revenue", "sales", "gross profit", "operating income", "eps")):
        return "income_statement"
    if any(term in lowered for term in ("margin", "ebitda", "roic")):
        return "key_metrics"
    return "unknown"


def document_id_for_url(url: str) -> str:
    """Generate a stable document id from a source URL."""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"doc_{digest}"


def fetch_primary_document(
    document: PrimaryDocument,
    artifact_root: Path,
    store: ArtifactStore | None = None,
    ticker: str = "",
) -> tuple[PrimaryDocument, bytes | None, str]:
    """Fetch a primary document and persist raw PDF bytes or source metadata."""
    store = store or LocalArtifactStore(artifact_root)
    if document.document_type == "pdf":
        response = requests.get(document.url, timeout=40, headers={"User-Agent": "ScopeResearchBot/0.1"})
        response.raise_for_status()
        document.local_path = store.write_bytes(
            ticker_artifact_key(ticker, "documents", "raw", f"{document.document_id}.pdf"),
            response.content,
            "application/pdf",
        )
        return document, response.content, ""

    details = scrape_site_detailed(document.url, allow_browser_fallback=document.is_primary_source, min_body_length=250)
    payload = {"url": document.url, "scrape": details}
    document.local_path = store.write_json(
        ticker_artifact_key(ticker, "documents", "raw", f"{document.document_id}.json"),
        payload,
    )
    return document, None, str(details.get("body", ""))
