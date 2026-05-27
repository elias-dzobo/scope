"""Tool facade used by the research harness.

The facade keeps the controller decoupled from today's pipeline implementation.
As the system becomes more agentic, individual methods can be split into finer
tools without changing the controller's high-level contract.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from pathlib import Path

from research_core.scoring.main import assess_pillars, build_stock_scorecard
from research_core.harness.documents import (
    DOCUMENT_QUERIES,
    DocumentParser,
    document_id_for_url,
    fetch_primary_document,
)
from research_core.harness.grounding import (
    build_grounded_prompt,
    call_gemini_grounded,
    parse_grounded_response,
)
from research_core.harness.models import (
    GroundedResearchResult,
    ParsedDocument,
    PrimaryDocument,
    ResearchBrief,
    ResearchWorkstream,
)
from research_core.legacy_pipeline.main import ProgressCallback, stock_research_pipeline
from research_core.storage import ArtifactStore, LocalArtifactStore, artifact_store, artifacts_dir, ticker_artifact_key
from provider_integrations.tools.main import search_tool
from research_core.utils.utils import _classify_candidate_source, _score_candidate


class ResearchToolFacade:
    """Expose current research capabilities behind harness-friendly methods."""

    def __init__(
        self,
        artifact_root: str | None = None,
        document_parser: DocumentParser | None = None,
        store: ArtifactStore | None = None,
    ) -> None:
        self.artifact_root = Path(artifact_root) if artifact_root is not None else artifacts_dir()
        self.document_parser = document_parser or DocumentParser()
        self.store = store or (LocalArtifactStore(self.artifact_root) if artifact_root is not None else artifact_store())

    def run_grounded_workstream_research(
        self,
        brief: ResearchBrief,
        workstream: ResearchWorkstream,
    ) -> GroundedResearchResult:
        """Run Gemini grounded research for one workstream."""
        prompt = build_grounded_prompt(brief, workstream)
        payload = call_gemini_grounded(prompt)
        return parse_grounded_response(payload, workstream, brief=brief)

    def discover_primary_documents(
        self,
        company_name: str,
        ticker: str,
        grounded_results: list[GroundedResearchResult] | None = None,
        max_documents: int = 8,
    ) -> list[PrimaryDocument]:
        """Discover canonical primary documents from grounding sources and search."""
        candidates: list[dict[str, Any]] = []
        for grounded in grounded_results or []:
            for source in grounded.sources:
                if source.url:
                    candidates.append({"title": source.title, "link": source.url, "snippet": grounded.answer[:500], "provider": "gemini_grounding"})

        candidates_lock = threading.Lock()

        def _search_one(discovery_type: str, template: str) -> list[dict[str, Any]]:
            query = template.format(company=company_name, ticker=ticker)
            results = []
            for result in search_tool({"query": query}):
                candidate = dict(result)
                candidate["discovery_type"] = discovery_type
                results.append(candidate)
            return results

        n_workers = min(len(DOCUMENT_QUERIES), 4)
        with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="doc_disc") as executor:
            futures = {
                executor.submit(_search_one, discovery_type, template): discovery_type
                for discovery_type, template in DOCUMENT_QUERIES
            }
            for future in as_completed(futures):
                try:
                    batch = future.result()
                    with candidates_lock:
                        candidates.extend(batch)
                except Exception:
                    pass  # skip failed discovery queries — we still have others

        return self._rank_document_candidates(company_name, ticker, candidates, max_documents=max_documents)

    def fetch_document(self, ticker: str, document: PrimaryDocument) -> tuple[PrimaryDocument, bytes | None, str]:
        """Fetch one primary document and persist raw content under artifacts."""
        return fetch_primary_document(
            document,
            self.artifact_root / ticker.strip().upper(),
            store=self.store,
            ticker=ticker,
        )

    def parse_document(
        self,
        ticker: str,
        document: PrimaryDocument,
        raw_bytes: bytes | None = None,
        html_text: str = "",
    ) -> ParsedDocument:
        """Parse and persist one primary document."""
        parsed = self.document_parser.parse_document(document, raw_bytes=raw_bytes, html_text=html_text)
        parsed_uri = self.store.write_text(
            ticker_artifact_key(ticker, "documents", "parsed", f"{document.document_id}.json"),
            parsed.model_dump_json(indent=2),
            "application/json",
        )
        tables_uri = self.store.write_json(
            ticker_artifact_key(ticker, "documents", "tables", f"{document.document_id}.json"),
            [table.model_dump(mode="json") for table in parsed.tables],
        )
        document.metadata["parsed_artifact_uri"] = parsed_uri
        document.metadata["tables_artifact_uri"] = tables_uri
        return parsed

    def extract_document_tables(self, parsed: ParsedDocument) -> list[dict[str, Any]]:
        """Return parsed table records as dictionaries for result payloads."""
        return [table.model_dump(mode="json") for table in parsed.tables]

    def extract_document_evidence(
        self,
        pillar: str,
        document: PrimaryDocument,
        parsed: ParsedDocument,
    ) -> list[dict[str, Any]]:
        """Extract deterministic evidence facts from a parsed primary document."""
        return self.document_parser.extract_evidence(pillar, document, parsed)

    def run_targeted_fallback_search(
        self,
        company_name: str,
        ticker: str,
        workstream: ResearchWorkstream,
    ) -> dict[str, Any]:
        """Run the existing pipeline narrowly for a weak workstream."""
        return self.run_existing_company_pipeline(
            company_name=company_name,
            ticker=ticker,
            selected_pillars=[workstream.pillar_name or workstream.title],
        )

    def assess_pillars(self, evidence_by_pillar: dict[str, list[dict]]) -> dict[str, dict]:
        """Assess pillars using the existing deterministic scoring layer."""
        return assess_pillars(evidence_by_pillar)

    def build_scorecard(
        self,
        company_name: str,
        ticker: str,
        pillar_assessments: dict[str, dict],
        evidence_by_pillar: dict[str, list[dict]],
    ) -> dict[str, Any]:
        """Build the final stock scorecard.

        The scoring layer now supports optional LLM-assisted synthesis behind a
        deterministic fallback. This facade preserves the harness contract while
        allowing `SCORECARD_LLM_ENABLED=true` to make recommendations less rigid.
        """
        return build_stock_scorecard(company_name, ticker, pillar_assessments, evidence_by_pillar)

    def run_existing_company_pipeline(
        self,
        company_name: str,
        ticker: str,
        selected_pillars: list[str] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Run the current deterministic company pipeline.

        This method is deliberately thin. It creates a stable seam where the
        harness can later call smaller tools such as search, scrape, evidence
        extraction, and scoring independently.
        """
        return stock_research_pipeline(
            stock_name=company_name,
            ticker=ticker,
            selected_pillars=selected_pillars,
            progress_callback=progress_callback,
        )

    def _rank_document_candidates(
        self,
        company_name: str,
        ticker: str,
        candidates: list[dict[str, Any]],
        max_documents: int,
    ) -> list[PrimaryDocument]:
        """Classify, dedupe, and rank document candidates."""
        entity_terms = set(_entity_terms(company_name, ticker))
        ranked: list[PrimaryDocument] = []
        seen: set[str] = set()
        for candidate in candidates:
            url = str(candidate.get("link") or candidate.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            relevance = _candidate_entity_relevance(candidate, company_name, ticker)
            if _is_sec_candidate(url) and not relevance["matched"]:
                continue
            classification = _classify_candidate_source({"link": url, "title": candidate.get("title", ""), "snippet": candidate.get("snippet", "")})
            candidate_payload = dict(candidate)
            candidate_payload.update(classification)
            score = _score_candidate(
                pillar="Financial Engine",
                candidate=candidate_payload,
                classification=classification,
                entity_terms=entity_terms,
            )
            if classification["is_primary_source"]:
                score += 20
            if classification["document_type"] == "pdf":
                score += 10
            if relevance["matched"]:
                score += 25
            else:
                score -= 20
            if score <= 0 and not classification["is_primary_source"]:
                continue
            ranked.append(
                PrimaryDocument(
                    document_id=document_id_for_url(url),
                    title=str(candidate.get("title", "")),
                    url=url,
                    document_type=classification["document_type"],
                    source_kind=classification["source_kind"],
                    discovery_type=str(candidate.get("discovery_type", candidate.get("provider", ""))),
                    is_primary_source=classification["is_primary_source"],
                    score=score,
                    metadata={
                        "provider": candidate.get("provider", ""),
                        "entity_match": relevance,
                    },
                )
            )
        ranked.sort(key=lambda item: (item.is_primary_source, item.document_type == "pdf", item.score), reverse=True)
        return ranked[:max_documents]


def _entity_terms(company_name: str, ticker: str) -> list[str]:
    """Return meaningful company identity terms for source filtering."""
    stopwords = {"plc", "inc", "corp", "corporation", "company", "ltd", "limited", "holdings", "group", "the"}
    terms = [token for token in re.findall(r"[a-z0-9]+", company_name.lower()) if len(token) >= 4 and token not in stopwords]
    ticker_clean = re.sub(r"[^a-z0-9]", "", ticker.lower())
    if len(ticker_clean) >= 4:
        terms.append(ticker_clean)
    return sorted(set(terms))


def _candidate_entity_relevance(candidate: dict[str, Any], company_name: str, ticker: str) -> dict[str, Any]:
    """Score whether a source candidate appears to belong to the requested company.

    Search providers can return high-trust SEC documents for unrelated companies
    when a short ticker or generic filing query matches. This guard favors exact
    company terms and only uses short tickers when they appear as standalone text.
    """
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("title", "link", "url", "snippet", "body", "description")
    ).lower()
    terms = _entity_terms(company_name, ticker)
    matched_terms = [term for term in terms if re.search(rf"\b{re.escape(term)}\b", text)]
    ticker_clean = re.sub(r"[^a-z0-9]", "", ticker.lower())
    ticker_matched = bool(ticker_clean and len(ticker_clean) >= 2 and re.search(rf"\b{re.escape(ticker_clean)}\b", text))
    return {
        "matched": bool(matched_terms or ticker_matched),
        "matched_terms": matched_terms,
        "ticker_matched": ticker_matched,
    }


def _is_sec_candidate(url: str) -> bool:
    """Return whether the candidate is an SEC filing URL."""
    return "sec.gov" in url.lower()
