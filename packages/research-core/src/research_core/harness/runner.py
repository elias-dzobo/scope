"""Research runners for the Gemini-first harness.

Two runners are available:

ResearchAgentLoop (preferred)
    Truly agentic loop that evaluates evidence quality after each workstream
    and runs QueryRefinementTool + retry when evidence is thin.  Uses
    ToolRegistry for dispatch — adding new tools requires no loop changes.
    Handles synthesis failure gracefully (synthesis_failed flag, not an
    exception), so the service layer can persist completed_partial state.

CompanyResearchRunner (legacy staged pipeline)
    Original fixed-sequence runner kept for backward compatibility.
    Tests that target it directly still pass.  New code should prefer
    ResearchAgentLoop.
"""

from __future__ import annotations

import json
import hashlib
import os
import time
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from research_core.harness.gates import ResearchQualityGates
from research_core.harness.memory import HarnessMemoryStore
from research_core.harness.models import (
    GroundedResearchResult,
    PrimaryDocument,
    ResearchBrief,
    ResearchObservation,
    ResearchPlan,
    WorkstreamStatus,
)
from research_core.harness.tools import ResearchToolFacade
from research_core.legacy_pipeline.main import ProgressCallback
from research_core.synthesis.final_synthesis import FinalSynthesisGenerator
from research_core.storage import LocalArtifactStore, ticker_artifact_key
from research_core.utils.logger import get_logger

logger = get_logger(__name__)

# Total wall-clock budget for one research run (seconds).  Configurable via env
# so production can raise it without a code deploy.  Stages check this before
# starting a new unit of work — they never abort mid-call, just stop scheduling
# more calls once the deadline is past.
_DEFAULT_RUN_TIMEOUT = 480  # 8 minutes


def _run_timeout() -> float:
    """Return the configured run timeout in seconds."""
    try:
        return float(os.getenv("RESEARCH_RUN_TIMEOUT_SECONDS", str(_DEFAULT_RUN_TIMEOUT)))
    except ValueError:
        return float(_DEFAULT_RUN_TIMEOUT)


# Maximum parallel workers for grounded workstream research.
def _workstream_workers() -> int:
    try:
        return max(1, int(os.getenv("RESEARCH_WORKSTREAM_WORKERS", "6")))
    except ValueError:
        return 6


# ---------------------------------------------------------------------------
# Module-level helpers (shared by both runners and SynthesisTool)
# ---------------------------------------------------------------------------

DEFAULT_PILLAR_ORDER = [
    "Macro & Industry",
    "Economic Moat",
    "Financial Engine",
    "Management & Capital Allocation",
    "Valuation",
    "Technical Analysis",
]


def _pillars_order_for_synthesis(summary: dict[str, Any]) -> list[str]:
    """Stable pillar ordering for synthesis prompts and UI."""
    selected = summary.get("selected_pillars") or []
    assessments = summary.get("pillar_assessments") or {}
    if selected:
        ordered = [p for p in selected if p in assessments]
        ordered.extend(p for p in assessments if p not in ordered)
        return ordered
    ordered = [p for p in DEFAULT_PILLAR_ORDER if p in assessments]
    ordered.extend(p for p in assessments if p not in ordered)
    return ordered

CORE_DOCUMENT_PILLARS = {"Financial Engine", "Management & Capital Allocation", "Valuation"}


class CompanyResearchRunner:
    """Run company research as staged grounded research plus primary documents."""

    def __init__(
        self,
        tools: ResearchToolFacade,
        gates: ResearchQualityGates,
        memory: HarnessMemoryStore,
    ) -> None:
        self.tools = tools
        self.gates = gates
        self.memory = memory

    def run(
        self,
        brief: ResearchBrief,
        plan: ResearchPlan,
        company_name: str,
        ticker: str,
        selected_pillars: list[str] | None = None,
        progress_callback: ProgressCallback | None = None,
        user_financial_profile: dict[str, Any] | None = None,
        user_risk_profile: dict[str, Any] | None = None,
        investor_context: dict[str, Any] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a staged company research run and return the API-compatible summary."""
        import datetime

        def _utc_iso() -> str:
            return datetime.datetime.now(datetime.timezone.utc).isoformat()

        selected_pillars = selected_pillars or [workstream.title for workstream in plan.workstreams]
        artifact_records: list[dict[str, Any]] = []

        # Resolve asset class from plan metadata (set by the planner).
        _raw_ac = plan.metadata.get("asset_class", "")
        if not _raw_ac and plan.entities:
            _raw_ac = plan.entities[0].asset_class or ""
        try:
            from research_core.harness.asset_class import AssetClass as _AC
            _asset_class = _AC(_raw_ac) if _raw_ac else _AC.EQUITY_STOCK
        except (ValueError, ImportError):
            from research_core.harness.asset_class import AssetClass as _AC
            _asset_class = _AC.EQUITY_STOCK

        # ── Stage timing tracking ────────────────────────────────────────────
        run_start = time.perf_counter()
        run_started_at = _utc_iso()
        stage_timings: dict[str, dict[str, Any]] = {}

        def _stage_start(name: str) -> float:
            t = time.perf_counter()
            stage_timings[name] = {"started_at": _utc_iso(), "_t": t}
            return t

        def _stage_end(name: str) -> float:
            entry = stage_timings.get(name, {})
            elapsed = time.perf_counter() - entry.get("_t", time.perf_counter())
            entry["completed_at"] = _utc_iso()
            entry["duration_seconds"] = round(elapsed, 3)
            return elapsed

        self._emit(
            progress_callback,
            "grounded_research",
            {
                "status": "running",
                "stage_progress": 1,
                "current_substep": "starting source research workstreams",
            },
        )
        _stage_start("grounded_research")
        grounded_results = self._run_grounded_workstreams(brief, plan, ticker, progress_callback)
        _stage_end("grounded_research")
        self._emit(
            progress_callback,
            "grounded_research",
            {
                "status": "completed",
                "stage_progress": 100,
                "current_substep": "source research workstreams complete",
            },
        )

        self._emit(
            progress_callback,
            "primary_documents",
            {
                "status": "running",
                "stage_progress": 5,
                "current_substep": "discovering annual reports, filings, and investor documents",
            },
        )
        _stage_start("primary_documents")
        documents = self.tools.discover_primary_documents(company_name, ticker, grounded_results=grounded_results)
        self._emit(
            progress_callback,
            "primary_documents",
            {
                "status": "running",
                "stage_progress": 35,
                "current_substep": f"found {len(documents)} primary-document candidates",
            },
        )
        parsed_documents, document_tables = self._fetch_and_parse_documents(ticker, documents, progress_callback, artifact_records)
        self._persist_document_index(ticker, documents, artifact_records)
        _stage_end("primary_documents")
        self._emit(
            progress_callback,
            "primary_documents",
            {
                "status": "completed",
                "stage_progress": 100,
                "current_substep": f"parsed {len(parsed_documents)} documents and {len(document_tables)} tables",
            },
        )

        self._emit(
            progress_callback,
            "document_evidence",
            {
                "status": "running",
                "stage_progress": 20,
                "current_substep": "merging grounded evidence with document facts",
            },
        )
        _stage_start("document_evidence")
        evidence_by_pillar = self._merge_grounded_and_document_evidence(
            plan=plan,
            grounded_results=grounded_results,
            documents=documents,
            parsed_documents=parsed_documents,
        )
        sources_by_pillar = self._build_sources_by_pillar(plan, grounded_results, documents)
        _stage_end("document_evidence")
        self._emit(
            progress_callback,
            "document_evidence",
            {
                "status": "completed",
                "stage_progress": 100,
                "current_substep": f"assembled evidence for {len(evidence_by_pillar)} pillars",
            },
        )
        self._emit(
            progress_callback,
            "quality_gates",
            {
                "status": "running",
                "stage_progress": 25,
                "current_substep": "checking citation support, document coverage, and evidence alignment",
            },
        )
        _stage_start("quality_gates")
        gate_result = self.gates.evaluate_company_research(
            plan,
            {
                "evidence_by_pillar": evidence_by_pillar,
                "sources_by_pillar": sources_by_pillar,
                "documents": [document.model_dump(mode="json") for document in documents],
                "document_tables": document_tables,
            },
        )
        plan.add_gate_result(gate_result)
        _stage_end("quality_gates")
        self._emit(
            progress_callback,
            "quality_gates",
            {
                "status": "completed",
                "stage_progress": 100,
                "current_substep": "quality gates passed" if gate_result.passed else "quality gates found evidence gaps",
            },
        )

        if not gate_result.passed:
            self._emit(
                progress_callback,
                "targeted_fallback",
                {
                    "status": "running",
                    "stage_progress": 15,
                    "current_substep": "running targeted fallback for weak workstreams",
                },
            )
            _stage_start("targeted_fallback")
            fallback_summary = self._run_targeted_fallbacks(
                company_name=company_name,
                ticker=ticker,
                plan=plan,
                evidence_by_pillar=evidence_by_pillar,
                sources_by_pillar=sources_by_pillar,
                progress_callback=progress_callback,
            )
            _stage_end("targeted_fallback")
            if fallback_summary.get("used_full_fallback"):
                self._emit(
                    progress_callback,
                    "targeted_fallback",
                    {
                        "status": "completed",
                        "stage_progress": 100,
                        "current_substep": "full legacy fallback completed",
                    },
                )
                return self._apply_final_synthesis(
                    fallback_summary["summary"],
                    progress_callback,
                    user_financial_profile=user_financial_profile,
                    user_risk_profile=user_risk_profile,
                    investor_context=investor_context,
                )
            retry_gate = self.gates.evaluate_company_research(
                plan,
                {
                    "evidence_by_pillar": evidence_by_pillar,
                    "sources_by_pillar": sources_by_pillar,
                    "documents": [document.model_dump(mode="json") for document in documents],
                    "document_tables": document_tables,
                },
            )
            plan.add_gate_result(retry_gate)
            self._emit(
                progress_callback,
                "targeted_fallback",
                {
                    "status": "completed",
                    "stage_progress": 100,
                    "current_substep": "targeted fallback complete",
                },
            )

        self._emit(
            progress_callback,
            "pillar_assessment",
            {
                "status": "running",
                "stage_progress": 30,
                "current_substep": "assessing six-pillar research signals",
            },
        )
        _stage_start("pillar_assessment")
        pillar_assessments = self.tools.assess_pillars(evidence_by_pillar, asset_class=_asset_class)
        _stage_end("pillar_assessment")
        self._emit(
            progress_callback,
            "pillar_assessment",
            {
                "status": "completed",
                "stage_progress": 100,
                "current_substep": f"assessed {len(pillar_assessments)} pillars",
            },
        )
        self._emit(
            progress_callback,
            "score",
            {
                "status": "running",
                "stage_progress": 40,
                "current_substep": "building final scorecard and recommendation",
            },
        )
        _stage_start("score")
        scorecard = self.tools.build_scorecard(
            company_name, ticker, pillar_assessments, evidence_by_pillar,
            asset_class=_asset_class,
        )
        _stage_end("score")

        run_completed_at = _utc_iso()
        total_duration = round(time.perf_counter() - run_start, 3)

        result = self._build_summary(
            ticker=ticker,
            company_name=company_name,
            selected_pillars=selected_pillars,
            grounded_results=grounded_results,
            documents=documents,
            parsed_documents=parsed_documents,
            document_tables=document_tables,
            evidence_by_pillar=evidence_by_pillar,
            sources_by_pillar=sources_by_pillar,
            pillar_assessments=pillar_assessments,
            scorecard=scorecard,
            artifact_records=artifact_records,
            stage_timings=stage_timings,
            run_started_at=run_started_at,
            run_completed_at=run_completed_at,
            total_duration=total_duration,
        )
        self._persist_evidence(ticker, evidence_by_pillar, artifact_records)
        self._emit(
            progress_callback,
            "score",
            {
                "status": "completed",
                "stage_progress": 100,
                "current_substep": "scorecard ready",
            },
        )
        plan.add_observation(
            ResearchObservation(
                stage="gemini_first_company_research",
                summary="Ran source research workstreams and primary-document extraction.",
                metrics={
                    "grounded_result_count": len(grounded_results),
                    "document_count": len(documents),
                    "table_count": len(document_tables),
                    "overall_score": scorecard.get("overall_score", 0),
                },
            )
        )
        return self._apply_final_synthesis(
            result,
            progress_callback,
            user_financial_profile=user_financial_profile,
            user_risk_profile=user_risk_profile,
            investor_context=investor_context,
            artifact_records=artifact_records,
        )

    @staticmethod
    def _pillars_order_for_synthesis(summary: dict[str, Any]) -> list[str]:
        """Stable pillar ordering for synthesis prompts and UI."""
        return _pillars_order_for_synthesis(summary)

    def _apply_final_synthesis(
        self,
        summary: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
        user_financial_profile: dict[str, Any] | None = None,
        user_risk_profile: dict[str, Any] | None = None,
        investor_context: dict[str, Any] | None = None,
        artifact_records: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Generate the investment memo.

        Failures are caught and recorded on the summary dict as
        ``synthesis_failed=True`` so the service layer can persist a
        ``completed_partial`` result rather than marking the run ``failed``.
        The scorecard and evidence are always available even when synthesis fails.
        """
        self._emit(
            progress_callback,
            "final_synthesis",
            {
                "status": "running",
                "stage_progress": 15,
                "current_substep": "writing user-facing investment memo",
            },
        )
        gen = FinalSynthesisGenerator()
        pillars_order = self._pillars_order_for_synthesis(summary)
        try:
            final = gen.generate(
                company_name=str(summary.get("stock_name", "")),
                ticker=str(summary.get("ticker", "")),
                scorecard=dict(summary.get("scorecard") or {}),
                pillar_assessments=dict(summary.get("pillar_assessments") or {}),
                evidence_by_pillar=dict(summary.get("evidence_by_pillar") or {}),
                sources_by_pillar=dict(summary.get("sources_by_pillar") or {}),
                pillars_order=pillars_order,
                user_financial_profile=user_financial_profile,
                user_risk_profile=user_risk_profile,
                investor_context=investor_context,
            )
            summary["final_synthesis"] = final
            try:
                uri = gen.persist_artifact(str(summary.get("ticker", "")), final)
                if artifact_records is not None:
                    self._record_artifact(
                        artifact_records,
                        artifact_type="final_synthesis",
                        storage_uri=uri,
                        content_type="application/json",
                        payload=final,
                        metadata={"ticker": summary.get("ticker", "")},
                    )
            except Exception:
                logger.exception("Failed to persist final synthesis artifact")
        except Exception as exc:
            logger.error(
                "Final synthesis failed — returning partial result | ticker=%s | error=%s",
                summary.get("ticker", ""),
                exc,
            )
            summary["synthesis_failed"] = True
            summary["synthesis_error"] = str(exc)

        if artifact_records is not None:
            summary["artifact_manifest"] = artifact_records
        self._emit(
            progress_callback,
            "final_synthesis",
            {
                "status": "completed" if not summary.get("synthesis_failed") else "partial",
                "stage_progress": 100,
                "current_substep": (
                    "investment memo ready"
                    if not summary.get("synthesis_failed")
                    else "synthesis failed — scorecard and evidence are still available"
                ),
            },
        )
        return summary

    def _run_grounded_workstreams(
        self,
        brief: ResearchBrief,
        plan: ResearchPlan,
        ticker: str,
        progress_callback: ProgressCallback | None = None,
    ) -> list[GroundedResearchResult]:
        grounded_results: list[GroundedResearchResult] = []
        total = max(len(plan.workstreams), 1)
        for index, workstream in enumerate(plan.workstreams, start=1):
            workstream.status = WorkstreamStatus.RUNNING
            self._emit(
                progress_callback,
                "grounded_research",
                {
                    "status": "running",
                    "stage_progress": round(((index - 1) / total) * 100, 1),
                    "current_substep": f"researching {workstream.title} ({index}/{total}) from current sources",
                },
            )
            grounded = self.tools.run_grounded_workstream_research(brief, workstream)
            grounded_results.append(grounded)
            workstream.mark_step_completed("grounded_research")
            self._emit(
                progress_callback,
                "grounded_research",
                {
                    "status": "running",
                    "stage_progress": round((index / total) * 100, 1),
                    "current_substep": f"{workstream.title}: {len(grounded.sources)} sources, {len(grounded.evidence_candidates)} evidence candidates",
                },
            )
            self.memory.append_trace(
                ticker,
                {
                    "event": "grounded_research",
                    "workstream_id": workstream.id,
                    "status": grounded.status,
                    "source_count": len(grounded.sources),
                    "evidence_candidate_count": len(grounded.evidence_candidates),
                },
            )
        return grounded_results

    def _fetch_and_parse_documents(
        self,
        ticker: str,
        documents: list[PrimaryDocument],
        progress_callback: ProgressCallback | None = None,
        artifact_records: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        parsed_documents: dict[str, Any] = {}
        all_tables: list[dict[str, Any]] = []

        if not documents:
            return parsed_documents, all_tables

        # Emit a single "starting" progress event before launching threads
        self._emit(
            progress_callback,
            "primary_documents",
            {
                "status": "running",
                "stage_progress": 36,
                "current_substep": f"fetching and parsing {len(documents)} documents in parallel",
            },
        )

        # Thread-local results — merged into shared structures after the pool exits
        thread_results: list[dict[str, Any] | None] = [None] * len(documents)
        thread_artifacts: list[list[dict[str, Any]]] = [[] for _ in documents]

        def _fetch_parse_one(index: int, document: PrimaryDocument) -> None:
            local_artifacts: list[dict[str, Any]] = []
            try:
                fetched, raw_bytes, html_text = self.tools.fetch_document(ticker, document)
                if fetched.local_path:
                    self._record_artifact(
                        local_artifacts,
                        artifact_type="raw_document",
                        storage_uri=fetched.local_path,
                        content_type="application/pdf" if fetched.document_type == "pdf" else "application/json",
                        payload=raw_bytes if raw_bytes is not None else html_text,
                        metadata={
                            "document_id": fetched.document_id,
                            "title": fetched.title,
                            "url": fetched.url,
                            "document_type": fetched.document_type,
                        },
                    )
                parsed = self.tools.parse_document(ticker, fetched, raw_bytes=raw_bytes, html_text=html_text)
                if fetched.metadata.get("parsed_artifact_uri"):
                    self._record_artifact(
                        local_artifacts,
                        artifact_type="parsed_document",
                        storage_uri=str(fetched.metadata["parsed_artifact_uri"]),
                        content_type="application/json",
                        payload=parsed.model_dump(mode="json"),
                        metadata={"document_id": fetched.document_id, "title": fetched.title},
                    )
                if fetched.metadata.get("tables_artifact_uri"):
                    tables_payload = [table.model_dump(mode="json") for table in parsed.tables]
                    self._record_artifact(
                        local_artifacts,
                        artifact_type="extracted_tables",
                        storage_uri=str(fetched.metadata["tables_artifact_uri"]),
                        content_type="application/json",
                        payload=tables_payload,
                        metadata={"document_id": fetched.document_id, "table_count": len(tables_payload)},
                    )
                thread_results[index] = {"document": document, "parsed": parsed}
                thread_artifacts[index] = local_artifacts
            except Exception as exc:
                document.metadata["fetch_error"] = str(exc)

        n_workers = min(len(documents), 4)
        with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="doc_fetch") as executor:
            futs = {executor.submit(_fetch_parse_one, i, doc): i for i, doc in enumerate(documents)}
            for fut in as_completed(futs):
                fut.result()  # re-raise unexpected thread errors; fetch errors are already caught

        # Merge results in original document order (preserves determinism)
        for result, local_artifacts in zip(thread_results, thread_artifacts):
            if result is None:
                continue
            doc = result["document"]
            parsed = result["parsed"]
            parsed_documents[doc.document_id] = parsed
            all_tables.extend(self.tools.extract_document_tables(parsed))
            if artifact_records is not None:
                artifact_records.extend(local_artifacts)

        # Emit a single completion event from the main thread
        self._emit(
            progress_callback,
            "primary_documents",
            {
                "status": "running",
                "stage_progress": 95,
                "current_substep": f"parsed {len(parsed_documents)}/{len(documents)} documents",
            },
        )
        return parsed_documents, all_tables

    def _merge_grounded_and_document_evidence(
        self,
        plan: ResearchPlan,
        grounded_results: list[GroundedResearchResult],
        documents: list[PrimaryDocument],
        parsed_documents: dict[str, Any],
    ) -> dict[str, list[dict]]:
        evidence_by_pillar: dict[str, list[dict]] = defaultdict(list)
        for grounded in grounded_results:
            pillar = grounded.pillar_name
            evidence_by_pillar[pillar].extend(grounded.evidence_candidates)
        for workstream in plan.workstreams:
            pillar = workstream.pillar_name or workstream.title
            for document in documents:
                parsed = parsed_documents.get(document.document_id)
                if not parsed:
                    continue
                evidence_by_pillar[pillar].extend(self.tools.extract_document_evidence(pillar, document, parsed))
        return dict(evidence_by_pillar)

    def _build_sources_by_pillar(
        self,
        plan: ResearchPlan,
        grounded_results: list[GroundedResearchResult],
        documents: list[PrimaryDocument],
    ) -> dict[str, list[dict]]:
        sources_by_pillar: dict[str, list[dict]] = defaultdict(list)
        for grounded in grounded_results:
            pillar = grounded.pillar_name
            for source in grounded.sources:
                sources_by_pillar[pillar].append(
                    {
                        "title": source.title,
                        "link": source.url,
                        "source_kind": source.source_kind,
                        "is_primary_source": False,
                        "document_type": "html",
                    }
                )
        for workstream in plan.workstreams:
            pillar = workstream.pillar_name or workstream.title
            for document in documents:
                sources_by_pillar[pillar].append(
                    {
                        "title": document.title,
                        "link": document.url,
                        "source_kind": document.source_kind,
                        "is_primary_source": document.is_primary_source,
                        "document_type": document.document_type,
                        "metadata": document.metadata,
                    }
                )
        return dict(sources_by_pillar)

    def _run_targeted_fallbacks(
        self,
        company_name: str,
        ticker: str,
        plan: ResearchPlan,
        evidence_by_pillar: dict[str, list[dict]],
        sources_by_pillar: dict[str, list[dict]],
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        weak = [
            workstream
            for workstream in plan.workstreams
            if workstream.status == WorkstreamStatus.NEEDS_MORE_EVIDENCE
        ]
        if len(weak) == len(plan.workstreams) and not any(evidence_by_pillar.values()):
            summary = self.tools.run_existing_company_pipeline(
                company_name=company_name,
                ticker=ticker,
                selected_pillars=[workstream.title for workstream in plan.workstreams],
                progress_callback=progress_callback,
            )
            fallback_gate = self.gates.evaluate_company_research(plan, summary)
            plan.add_gate_result(fallback_gate)
            return {
                "used_full_fallback": True,
                "summary": summary,
            }
        retry_workstreams = weak[:2]
        total = max(len(retry_workstreams), 1)
        self._emit(
            progress_callback,
            "targeted_fallback",
            {
                "status": "running",
                "stage_progress": 5,
                "current_substep": f"running {total} targeted fallback search(es) in parallel",
            },
        )

        fallback_results: dict[str, dict] = {}

        def _run_fallback(workstream: Any) -> tuple[str, dict]:
            result = self.tools.run_targeted_fallback_search(company_name, ticker, workstream)
            return workstream.pillar_name or workstream.title, result

        with ThreadPoolExecutor(max_workers=min(total, 4), thread_name_prefix="fallback") as executor:
            futures = {executor.submit(_run_fallback, ws): ws for ws in retry_workstreams}
            done_count = 0
            for future in as_completed(futures):
                ws = futures[future]
                done_count += 1
                try:
                    pillar, fallback = future.result()
                    fallback_results[pillar] = fallback
                    ws.mark_step_completed("targeted_fallback_search")
                except Exception as exc:
                    logger.warning("Targeted fallback failed for %s: %s", ws.title, exc)
                self._emit(
                    progress_callback,
                    "targeted_fallback",
                    {
                        "status": "running",
                        "stage_progress": round((done_count / total) * 95, 1),
                        "current_substep": f"fallback complete for {ws.title} ({done_count}/{total})",
                    },
                )

        for workstream in retry_workstreams:
            pillar = workstream.pillar_name or workstream.title
            if pillar not in fallback_results:
                continue
            fallback = fallback_results[pillar]
            evidence_by_pillar[pillar].extend(fallback.get("evidence_by_pillar", {}).get(pillar, []))
            sources_by_pillar[pillar].extend(fallback.get("sources_by_pillar", {}).get(pillar, []))

        return {"used_full_fallback": False}

    def _build_summary(
        self,
        ticker: str,
        company_name: str,
        selected_pillars: list[str],
        grounded_results: list[GroundedResearchResult],
        documents: list[PrimaryDocument],
        parsed_documents: dict[str, Any],
        document_tables: list[dict[str, Any]],
        evidence_by_pillar: dict[str, list[dict]],
        sources_by_pillar: dict[str, list[dict]],
        pillar_assessments: dict[str, dict],
        scorecard: dict[str, Any],
        artifact_records: list[dict[str, Any]] | None = None,
        stage_timings: dict[str, dict[str, Any]] | None = None,
        run_started_at: str = "",
        run_completed_at: str = "",
        total_duration: float = 0.0,
    ) -> dict[str, Any]:
        st = stage_timings or {}

        def _step(stage: str, output_summary: dict[str, Any]) -> dict[str, Any]:
            entry = st.get(stage, {})
            return {
                "stage": stage,
                "status": "completed",
                "started_at": entry.get("started_at", ""),
                "completed_at": entry.get("completed_at", ""),
                "duration_seconds": entry.get("duration_seconds", 0),
                "input_summary": {},
                "output_summary": output_summary,
            }

        steps = [
            _step("grounded_research", {"grounded_result_count": len(grounded_results)}),
            _step("primary_documents", {
                "document_count": len(documents),
                "parsed_document_count": len(parsed_documents),
                "table_count": len(document_tables),
            }),
            _step("document_evidence", {"pillar_count": len(evidence_by_pillar)}),
            _step("score", {"overall_score": scorecard.get("overall_score", 0)}),
        ]
        if "quality_gates" in st:
            steps.insert(2, _step("quality_gates", {}))
        if "targeted_fallback" in st:
            steps.insert(3, _step("targeted_fallback", {}))
        if "pillar_assessment" in st:
            steps.append(_step("pillar_assessment", {"pillar_count": len(pillar_assessments)}))

        return {
            "ticker": ticker,
            "stock_name": company_name,
            "selected_pillars": selected_pillars,
            "query_batch_count": sum(len(item.web_search_queries) for item in grounded_results),
            "primary_source_count": sum(1 for item in documents if item.is_primary_source),
            "filtered_doc_count": len(documents),
            "scraped_doc_count": len(parsed_documents),
            "overall_score": scorecard.get("overall_score", 0),
            "recommendation": scorecard.get("recommendation", "Insufficient Data"),
            "scorecard": scorecard,
            "pillar_assessments": pillar_assessments,
            "evidence_by_pillar": evidence_by_pillar,
            "sources_by_pillar": sources_by_pillar,
            "documents": [document.model_dump(mode="json") for document in documents],
            "document_tables": document_tables,
            "grounded_results": [item.model_dump(mode="json") for item in grounded_results],
            "artifact_manifest": artifact_records or [],
            "runtime_profile": {
                "stock_name": company_name,
                "ticker": ticker,
                "started_at": run_started_at,
                "completed_at": run_completed_at,
                "total_duration_seconds": total_duration,
                "steps": steps,
                "slowest_steps": sorted(steps, key=lambda s: s["duration_seconds"], reverse=True)[:3],
                "step_share": [],
                "parallelism_candidates": [],
            },
        }

    def _persist_document_index(
        self,
        ticker: str,
        documents: list[PrimaryDocument],
        artifact_records: list[dict[str, Any]] | None = None,
    ) -> None:
        output = self.memory.artifact_root / ticker.strip().upper() / "documents"
        payload = [document.model_dump(mode="json") for document in documents]
        if isinstance(self.memory.store, LocalArtifactStore):
            output.mkdir(parents=True, exist_ok=True)
            uri = str(output / "document_index.json")
            (output / "document_index.json").write_text(json.dumps(payload, indent=2))
        else:
            uri = self.memory.store.write_json(ticker_artifact_key(ticker, "documents", "document_index.json"), payload)
        if artifact_records is not None:
            self._record_artifact(
                artifact_records,
                artifact_type="document_index",
                storage_uri=uri,
                content_type="application/json",
                payload=payload,
                metadata={"document_count": len(documents)},
            )

    def _persist_evidence(
        self,
        ticker: str,
        evidence_by_pillar: dict[str, list[dict]],
        artifact_records: list[dict[str, Any]] | None = None,
    ) -> None:
        output = self.memory.artifact_root / ticker.strip().upper() / "evidence"
        if isinstance(self.memory.store, LocalArtifactStore):
            output.mkdir(parents=True, exist_ok=True)
            uri = str(output / "document_facts.json")
            (output / "document_facts.json").write_text(json.dumps(evidence_by_pillar, indent=2))
        else:
            uri = self.memory.store.write_json(ticker_artifact_key(ticker, "evidence", "document_facts.json"), evidence_by_pillar)
        if artifact_records is not None:
            self._record_artifact(
                artifact_records,
                artifact_type="document_evidence",
                storage_uri=uri,
                content_type="application/json",
                payload=evidence_by_pillar,
                metadata={"pillar_count": len(evidence_by_pillar)},
            )

    def _record_artifact(
        self,
        records: list[dict[str, Any]],
        *,
        artifact_type: str,
        storage_uri: str,
        content_type: str,
        payload: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append manifest-ready metadata for an artifact written by the harness."""
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        backend = getattr(self.memory.store, "backend_name", "local" if isinstance(self.memory.store, LocalArtifactStore) else "s3")
        records.append(
            {
                "artifact_type": artifact_type,
                "storage_backend": backend,
                "storage_uri": storage_uri,
                "content_type": content_type,
                "size_bytes": len(data),
                "checksum": hashlib.sha256(data).hexdigest(),
                "metadata": metadata or {},
            }
        )

    def _emit(self, progress_callback: ProgressCallback | None, stage: str, payload: dict[str, Any]) -> None:
        if progress_callback:
            progress_callback(stage, payload)


# ---------------------------------------------------------------------------
# ResearchAgentLoop — truly agentic, tool-registry-driven
# ---------------------------------------------------------------------------


class ResearchAgentLoop:
    """Agentic research loop that selects and sequences tools based on evidence quality.

    Key differences from CompanyResearchRunner
    -------------------------------------------
    1. Per-workstream quality evaluation: after each grounded search, the loop
       checks ``ToolResult.quality_score``. If evidence is thin it calls
       ``QueryRefinementTool`` and retries — up to ``workstream.max_iterations``.

    2. Gate → replan loop: after evidence assembly, the quality gate is run.
       If it fails, ``_replan_from_gate_gaps`` updates weak workstreams with
       targeted query_hints and re-runs those workstreams for one more iteration.

    3. Graceful synthesis failure: ``SynthesisTool`` catches its own exceptions
       and sets ``synthesis_failed=True`` on the summary. The service layer
       detects this and writes ``completed_partial`` instead of ``failed``.

    4. ToolRegistry dispatch: all tool calls go through the registry, so adding
       a new capability (e.g. SEC EDGAR direct fetcher) requires only registering
       a new tool — no loop changes needed.
    """

    def __init__(
        self,
        tools: ResearchToolFacade,
        gates: ResearchQualityGates,
        memory: HarnessMemoryStore,
    ) -> None:
        from research_core.harness.tool_registry import ToolRegistry

        self.tools = tools
        self.gates = gates
        self.memory = memory
        self.registry = ToolRegistry(tools, gates)

    def run(
        self,
        brief: ResearchBrief,
        plan: ResearchPlan,
        company_name: str,
        ticker: str,
        selected_pillars: list[str] | None = None,
        progress_callback: ProgressCallback | None = None,
        user_financial_profile: dict[str, Any] | None = None,
        user_risk_profile: dict[str, Any] | None = None,
        investor_context: dict[str, Any] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute the agentic research loop and return the API-compatible summary."""
        import datetime

        def _utc_iso() -> str:
            return datetime.datetime.now(datetime.timezone.utc).isoformat()

        selected_pillars = selected_pillars or [ws.title for ws in plan.workstreams]
        artifact_records: list[dict[str, Any]] = []
        deadline = time.monotonic() + _run_timeout()

        # ── Stage timing tracking ────────────────────────────────────────────
        run_start = time.perf_counter()
        run_started_at = _utc_iso()
        stage_timings: dict[str, dict[str, Any]] = {}

        def _stage_start(name: str) -> float:
            t = time.perf_counter()
            stage_timings[name] = {"started_at": _utc_iso(), "_t": t}
            return t

        def _stage_end(name: str) -> float:
            entry = stage_timings.get(name, {})
            elapsed = time.perf_counter() - entry.get("_t", time.perf_counter())
            entry["completed_at"] = _utc_iso()
            entry["duration_seconds"] = round(elapsed, 3)
            return elapsed

        # ── Stage 1: per-workstream grounded search with retry ──────────────
        # Fire document discovery search queries concurrently with grounded
        # research so the two I/O-heavy phases overlap. Results are collected
        # after grounded research completes and forwarded to the discovery tool.
        _doc_search_candidates: list[dict] = []
        _doc_search_error: list[BaseException] = []

        def _prefetch_doc_search() -> None:
            try:
                _doc_search_candidates.extend(
                    self.tools.run_document_search_queries(company_name, ticker)
                )
            except Exception as exc:
                _doc_search_error.append(exc)

        _doc_prefetch_thread = threading.Thread(
            target=_prefetch_doc_search,
            daemon=True,
            name=f"doc-prefetch-{ticker}",
        )
        _doc_prefetch_thread.start()

        self._emit(progress_callback, "grounded_research", {
            "status": "running", "stage_progress": 1,
            "current_substep": "starting agentic source research workstreams",
        })
        _stage_start("grounded_research")
        grounded_results = self._run_workstreams_with_retries(
            brief, plan, ticker, progress_callback, deadline=deadline
        )
        _stage_end("grounded_research")
        self._emit(progress_callback, "grounded_research", {
            "status": "completed", "stage_progress": 100,
            "current_substep": f"completed {len(grounded_results)} workstreams",
        })

        # Join the prefetch thread (should already be done by now).
        _doc_prefetch_thread.join(timeout=30)
        if _doc_search_error:
            logger.warning("Document search prefetch failed: %s", _doc_search_error[0])

        # ── Stage 2: primary document discovery ─────────────────────────────
        self._emit(progress_callback, "primary_documents", {
            "status": "running", "stage_progress": 5,
            "current_substep": "discovering annual reports and filings",
        })
        _stage_start("primary_documents")
        doc_result = self.registry.get("document_discovery").run(
            self._make_ctx(brief, ticker=ticker, company_name=company_name,
                           grounded_results=grounded_results),
            grounded_results=grounded_results,
            pre_fetched_candidates=_doc_search_candidates if _doc_search_candidates else None,
        )
        documents: list = doc_result.output or []
        self._emit(progress_callback, "primary_documents", {
            "status": "running", "stage_progress": 35,
            "current_substep": f"found {len(documents)} document candidates",
        })

        # ── Stage 3: fetch, parse, and extract evidence from documents ───────
        from research_core.harness.agent_tools import ToolContext
        base_ctx = ToolContext(
            brief=brief,
            workstream=plan.workstreams[0] if plan.workstreams else self._dummy_workstream(),
            ticker=ticker,
            company_name=company_name,
            grounded_results=grounded_results,
            documents=documents,
            artifact_records=artifact_records,
        )
        parsed_documents, document_tables = self._fetch_parse_extract(
            base_ctx, documents, plan, progress_callback, artifact_records
        )
        self._persist_doc_index(ticker, documents, artifact_records)
        _stage_end("primary_documents")
        self._emit(progress_callback, "primary_documents", {
            "status": "completed", "stage_progress": 100,
            "current_substep": f"parsed {len(parsed_documents)} documents, {len(document_tables)} tables",
        })

        # ── Stage 4: assemble evidence ───────────────────────────────────────
        self._emit(progress_callback, "document_evidence", {
            "status": "running", "stage_progress": 20,
            "current_substep": "merging grounded evidence with document facts",
        })
        _stage_start("document_evidence")
        evidence_by_pillar = self._merge_evidence(
            plan, grounded_results, documents, parsed_documents
        )
        sources_by_pillar = self._build_sources(plan, grounded_results, documents)
        _stage_end("document_evidence")
        self._emit(progress_callback, "document_evidence", {
            "status": "completed", "stage_progress": 100,
            "current_substep": f"assembled evidence for {len(evidence_by_pillar)} pillars",
        })

        # ── Stage 5: quality gate → replan loop ──────────────────────────────
        _stage_start("quality_gates")
        evidence_by_pillar, sources_by_pillar = self._run_gate_replan_loop(
            brief=brief,
            plan=plan,
            evidence_by_pillar=evidence_by_pillar,
            sources_by_pillar=sources_by_pillar,
            documents=documents,
            document_tables=document_tables,
            progress_callback=progress_callback,
            deadline=deadline,
        )
        _stage_end("quality_gates")

        # ── Stage 6: pillar scoring ───────────────────────────────────────────
        self._emit(progress_callback, "pillar_assessment", {
            "status": "running", "stage_progress": 30,
            "current_substep": "assessing six-pillar research signals",
        })
        _stage_start("pillar_assessment")
        score_result = self.registry.get("pillar_scoring").run(
            base_ctx, company_name=company_name, evidence_by_pillar=evidence_by_pillar
        )
        pillar_assessments = score_result.output["pillar_assessments"]
        scorecard = score_result.output["scorecard"]
        _stage_end("pillar_assessment")
        self._emit(progress_callback, "pillar_assessment", {
            "status": "completed", "stage_progress": 100,
            "current_substep": f"assessed {len(pillar_assessments)} pillars",
        })
        self._emit(progress_callback, "score", {
            "status": "running", "stage_progress": 40,
            "current_substep": "building final scorecard",
        })
        _stage_start("score")

        run_completed_at = _utc_iso()
        total_duration = round(time.perf_counter() - run_start, 3)
        _stage_end("score")

        # Build summary dict — scorecard always present regardless of synthesis
        summary = self._build_summary(
            ticker=ticker,
            company_name=company_name,
            selected_pillars=selected_pillars,
            grounded_results=grounded_results,
            documents=documents,
            parsed_documents=parsed_documents,
            document_tables=document_tables,
            evidence_by_pillar=evidence_by_pillar,
            sources_by_pillar=sources_by_pillar,
            pillar_assessments=pillar_assessments,
            scorecard=scorecard,
            artifact_records=artifact_records,
            stage_timings=stage_timings,
            run_started_at=run_started_at,
            run_completed_at=run_completed_at,
            total_duration=total_duration,
        )
        self._persist_evidence(ticker, evidence_by_pillar, artifact_records)
        self._emit(progress_callback, "score", {
            "status": "completed", "stage_progress": 100,
            "current_substep": "scorecard ready",
        })

        plan.add_observation(ResearchObservation(
            stage="agent_loop_research",
            summary="Agentic loop: grounded search with retries, gate replan, and scoring.",
            metrics={
                "grounded_result_count": len(grounded_results),
                "document_count": len(documents),
                "table_count": len(document_tables),
                "overall_score": scorecard.get("overall_score", 0),
            },
        ))

        # ── Stage 7: synthesis (fault-tolerant) ──────────────────────────────
        self._emit(progress_callback, "final_synthesis", {
            "status": "running", "stage_progress": 15,
            "current_substep": "writing investment memo",
        })
        synth_result = self.registry.get("synthesis").run(
            base_ctx,
            summary=summary,
            user_financial_profile=user_financial_profile,
            user_risk_profile=user_risk_profile,
            investor_context=investor_context,
        )
        if synth_result.success:
            summary["final_synthesis"] = synth_result.output
            # Persist synthesis artifact
            try:
                gen = FinalSynthesisGenerator()
                uri = gen.persist_artifact(ticker, synth_result.output)
                self._record_artifact_to(
                    artifact_records,
                    artifact_type="final_synthesis",
                    storage_uri=uri,
                    content_type="application/json",
                    payload=synth_result.output,
                    metadata={"ticker": ticker},
                )
            except Exception:
                logger.exception("Failed to persist synthesis artifact")
        else:
            summary["synthesis_failed"] = True
            summary["synthesis_error"] = synth_result.error

        summary["artifact_manifest"] = artifact_records
        self._emit(progress_callback, "final_synthesis", {
            "status": "completed" if synth_result.success else "partial",
            "stage_progress": 100,
            "current_substep": (
                "investment memo ready"
                if synth_result.success
                else "synthesis failed — scorecard and evidence available"
            ),
        })
        return summary

    # ── Per-workstream agentic search with refinement retries ───────────────

    def _run_workstreams_with_retries(
        self,
        brief: ResearchBrief,
        plan: ResearchPlan,
        ticker: str,
        progress_callback: ProgressCallback | None,
        deadline: float | None = None,
    ) -> list[GroundedResearchResult]:
        """Run all workstreams in parallel, retrying with refined queries on thin evidence.

        Workstreams are dispatched concurrently via ThreadPoolExecutor so the
        6-pillar grounded search takes roughly as long as the slowest single
        pillar instead of the sum of all pillars.

        A shared ``completed`` counter drives coarse progress updates — each
        workstream emits a completion event under a lock so the DB writer stays
        safe across threads.
        """
        from research_core.harness.agent_tools import ToolContext

        workstreams = plan.workstreams
        total = max(len(workstreams), 1)
        company_name = brief.entities[0].name if brief.entities else ""

        # Ordered results slot — filled by index so order is preserved.
        grounded_results: list[GroundedResearchResult | None] = [None] * total
        completed_count = [0]
        progress_lock = threading.Lock()

        def _run_one(index: int, workstream: Any) -> tuple[int, GroundedResearchResult]:
            workstream.status = WorkstreamStatus.RUNNING
            ctx = ToolContext(
                brief=brief,
                workstream=workstream,
                ticker=ticker,
                company_name=company_name,
            )
            grounded = self._run_single_workstream(ctx, workstream, deadline=deadline)
            workstream.mark_step_completed("grounded_research")
            return index, grounded

        n_workers = min(total, _workstream_workers())
        self._emit(progress_callback, "grounded_research", {
            "status": "running",
            "stage_progress": 5,
            "current_substep": f"launching {total} workstreams across {n_workers} parallel workers",
        })

        with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="ws") as executor:
            futures = {
                executor.submit(_run_one, i, ws): ws
                for i, ws in enumerate(workstreams)
            }
            for future in as_completed(futures):
                try:
                    idx, grounded = future.result()
                except Exception as exc:
                    # Defensive: should not happen since _run_single_workstream
                    # catches its own errors, but guard anyway.
                    ws = futures[future]
                    grounded = GroundedResearchResult(
                        workstream_id=ws.id,
                        pillar_name=ws.pillar_name or ws.title,
                        status="failed",
                        error_message=str(exc),
                    )
                    idx = workstreams.index(ws)

                grounded_results[idx] = grounded
                ws = workstreams[idx]

                with progress_lock:
                    completed_count[0] += 1
                    done = completed_count[0]
                    self._emit(progress_callback, "grounded_research", {
                        "status": "running",
                        "stage_progress": round((done / total) * 95, 1),
                        "current_substep": (
                            f"{ws.title}: {len(grounded.sources)} sources, "
                            f"{len(grounded.evidence_candidates)} evidence candidates "
                            f"({done}/{total} done)"
                        ),
                    })

                self.memory.append_trace(ticker, {
                    "event": "grounded_research",
                    "workstream_id": ws.id,
                    "status": grounded.status,
                    "source_count": len(grounded.sources),
                    "evidence_candidate_count": len(grounded.evidence_candidates),
                    "iterations": ws.iteration_count,
                })

        # Replace any None slots (should not happen) with empty results.
        return [
            r if r is not None else GroundedResearchResult(
                workstream_id=workstreams[i].id,
                pillar_name=workstreams[i].pillar_name or workstreams[i].title,
                status="failed",
                error_message="workstream future did not complete",
            )
            for i, r in enumerate(grounded_results)
        ]

    def _run_single_workstream(
        self,
        ctx: "ToolContext",  # noqa: F821
        workstream: Any,
        deadline: float | None = None,
    ) -> GroundedResearchResult:
        """Run one workstream, refining queries on thin evidence.

        Progress callbacks are intentionally omitted here — this method now runs
        inside a thread pool and progress is reported by the caller after the
        future resolves (avoiding lock contention on every retry emission).

        The ``deadline`` parameter is a ``time.monotonic()`` value.  Each retry
        iteration checks it before starting a new Gemini call so the loop can
        exit early without aborting an in-flight request.
        """
        from research_core.harness.agent_tools import ToolContext

        # Determine which search tool to use based on tool_assignment
        primary_tool = workstream.tool_assignment  # "grounded_search" | "web_search"
        if primary_tool == "legacy_pipeline":
            primary_tool = "grounded_search"  # legacy pipeline is no longer used

        last_result: GroundedResearchResult | None = None
        max_iters = workstream.max_iterations

        for iteration in range(max_iters):
            # Deadline check — stop scheduling new calls once we're over budget.
            if deadline is not None and time.monotonic() > deadline:
                logger.warning(
                    "Run deadline exceeded before workstream retry | workstream=%s iteration=%d",
                    workstream.title,
                    iteration,
                )
                break

            ctx.iteration = iteration
            result = self.registry.get(primary_tool).run(ctx)

            if result.success and isinstance(result.output, GroundedResearchResult):
                # GroundedSearchTool returns a GroundedResearchResult — the only
                # type we can use as last_result.  WebSearchTool returns a
                # list[dict] of raw hits; we intentionally skip that here so
                # last_result is always a GroundedResearchResult (or None →
                # fallback below).
                last_result = result.output
                if result.quality_score >= 0.4 or iteration >= max_iters - 1:
                    # Good enough or exhausted retries
                    break

                # Thin evidence — refine and retry
                gaps = [
                    f"thin_content: only {len(last_result.evidence_candidates)} facts found"
                ]
                self.registry.get("query_refinement").run(
                    ctx, gaps=gaps,
                    current_evidence_count=len(last_result.evidence_candidates)
                )
            else:
                # Tool failed, returned wrong output type, or returned an empty
                # web-search list — refine and fall back to web_search.
                self.registry.get("query_refinement").run(
                    ctx,
                    gaps=[f"search_failed: {result.error or 'no usable output'}"],
                )
                if primary_tool != "web_search":
                    primary_tool = "web_search"

        if last_result is None:
            last_result = GroundedResearchResult(
                workstream_id=workstream.id,
                pillar_name=workstream.pillar_name or workstream.title,
                status="failed",
                error_message="All search iterations failed",
            )
        return last_result

    # ── Gate → replan loop ───────────────────────────────────────────────────

    def _run_gate_replan_loop(
        self,
        brief: ResearchBrief,
        plan: ResearchPlan,
        evidence_by_pillar: dict[str, list[dict]],
        sources_by_pillar: dict[str, list[dict]],
        documents: list,
        document_tables: list,
        progress_callback: ProgressCallback | None,
        deadline: float | None = None,
    ) -> tuple[dict, dict]:
        """Run quality gate; replan weak workstreams if it fails; run one retry pass."""
        from research_core.harness.agent_tools import ToolContext

        max_plan_iters = plan.stop_conditions.max_plan_iterations
        ticker = brief.entities[0].ticker if brief.entities else ""
        company_name = brief.entities[0].name if brief.entities else ""

        for gate_iter in range(max_plan_iters):
            # Bail out of the gate loop if we have already burned the time budget.
            if deadline is not None and time.monotonic() > deadline:
                logger.warning(
                    "Run deadline exceeded — skipping gate replan iteration %d", gate_iter + 1
                )
                self._emit(progress_callback, "quality_gates", {
                    "status": "skipped", "stage_progress": 100,
                    "current_substep": "time budget exhausted — skipping further gate iterations",
                })
                break

            self._emit(progress_callback, "quality_gates", {
                "status": "running", "stage_progress": 25,
                "current_substep": f"evaluating evidence quality (pass {gate_iter + 1})",
            })
            dummy_ctx = ToolContext(
                brief=brief,
                workstream=plan.workstreams[0] if plan.workstreams else self._dummy_workstream(),
                ticker=ticker,
                company_name=company_name,
                evidence_by_pillar=evidence_by_pillar,
            )
            gate_tool = self.registry.get("evidence_quality")
            gate_tool_result = gate_tool.run(
                dummy_ctx, plan=plan,
                evidence_by_pillar=evidence_by_pillar,
                sources_by_pillar=sources_by_pillar,
                documents=[d.model_dump(mode="json") if hasattr(d, "model_dump") else d for d in documents],
                document_tables=document_tables,
            )

            if not gate_tool_result.success:
                self._emit(progress_callback, "quality_gates", {
                    "status": "completed", "stage_progress": 100,
                    "current_substep": "quality gate check failed (non-fatal)",
                })
                break

            gate_result = gate_tool_result.output
            self._emit(progress_callback, "quality_gates", {
                "status": "completed" if gate_result.passed else "retrying",
                "stage_progress": 100,
                "current_substep": (
                    "quality gates passed"
                    if gate_result.passed
                    else f"gaps found: {gate_result.gaps[:2]}"
                ),
            })

            if gate_result.passed:
                break

            if gate_iter < max_plan_iters - 1:
                # Replan weak workstreams and run them
                replanned = self._replan_from_gate_gaps(brief, plan, gate_result)
                if not replanned:
                    break  # Nothing to replan — accept current state

                self._emit(progress_callback, "targeted_fallback", {
                    "status": "running", "stage_progress": 10,
                    "current_substep": f"replanning {len(replanned)} weak workstreams",
                })
                retry_results = self._run_workstreams_with_retries(
                    brief,
                    _plan_with_workstreams(plan, replanned),
                    ticker,
                    progress_callback,
                    deadline=deadline,
                )
                # Merge new results into evidence
                for gr in retry_results:
                    pillar = gr.pillar_name
                    evidence_by_pillar.setdefault(pillar, []).extend(gr.evidence_candidates)
                    for src in gr.sources:
                        sources_by_pillar.setdefault(pillar, []).append({
                            "title": src.title,
                            "link": src.url,
                            "source_kind": src.source_kind,
                            "is_primary_source": False,
                            "document_type": "html",
                        })
                self._emit(progress_callback, "targeted_fallback", {
                    "status": "completed", "stage_progress": 100,
                    "current_substep": "replan workstreams complete",
                })
            else:
                self._emit(progress_callback, "quality_gates", {
                    "status": "exhausted", "stage_progress": 100,
                    "current_substep": f"gate exhausted after {gate_iter + 1} iterations",
                })
                logger.warning(
                    "Gate exhausted after %d iterations | gaps=%s",
                    gate_iter + 1,
                    gate_result.gaps,
                )
                break

        return evidence_by_pillar, sources_by_pillar

    def _replan_from_gate_gaps(
        self,
        brief: ResearchBrief,
        plan: ResearchPlan,
        gate_result: Any,
    ) -> list[Any]:
        """Mark weak workstreams as PENDING with new query_hints.

        Returns the list of workstreams that were replanned.
        """
        from research_core.harness.agent_tools import derive_query_hints

        entity_name = brief.entities[0].name if brief.entities else ""
        ticker = brief.entities[0].ticker if brief.entities else ""
        replanned = []

        for gap in gate_result.gaps:
            gap_lower = gap.lower()
            for ws in plan.workstreams:
                pillar = (ws.pillar_name or ws.title).lower()
                if pillar in gap_lower or ws.id in gap:
                    hints = derive_query_hints(
                        company_name=entity_name,
                        ticker=ticker,
                        pillar=ws.pillar_name or ws.title,
                        gaps=[gap],
                        search_focus=ws.search_focus,
                        iteration=ws.iteration_count,
                    )
                    ws.query_hints = hints
                    ws.status = WorkstreamStatus.PENDING
                    ws.iteration_count += 1
                    if ws not in replanned:
                        replanned.append(ws)
                    logger.info(
                        "Replan workstream=%s hints=%s gap=%s",
                        ws.title,
                        hints,
                        gap[:80],
                    )
                    break

        return replanned

    # ── Document pipeline helpers ────────────────────────────────────────────

    def _fetch_parse_extract(
        self,
        base_ctx: Any,
        documents: list,
        plan: ResearchPlan,
        progress_callback: ProgressCallback | None,
        artifact_records: list,
    ) -> tuple[dict, list]:
        from research_core.harness.agent_tools import ToolContext

        parsed_documents: dict = {}
        all_tables: list = []

        if not documents:
            return parsed_documents, all_tables

        fetch_tool = self.registry.get("document_fetch")
        parse_tool = self.registry.get("document_parse")

        # Emit a single "starting" progress event before launching threads
        self._emit(progress_callback, "primary_documents", {
            "status": "running",
            "stage_progress": 36,
            "current_substep": f"fetching and parsing {len(documents)} documents in parallel",
        })

        # Thread-local results — merged into shared structures after the pool exits
        thread_results: list[dict[str, Any] | None] = [None] * len(documents)
        thread_artifacts: list[list[dict[str, Any]]] = [[] for _ in documents]

        def _fetch_parse_one(index: int, document: Any) -> None:
            local_artifacts: list[dict[str, Any]] = []
            ctx = ToolContext(
                brief=base_ctx.brief,
                workstream=plan.workstreams[0] if plan.workstreams else self._dummy_workstream(),
                ticker=base_ctx.ticker,
                company_name=base_ctx.company_name,
                artifact_records=[],  # isolated per thread; merged after
            )
            fetch_result = fetch_tool.run(ctx, document=document)
            if not fetch_result.success:
                return

            fetched = fetch_result.output["document"]
            raw_bytes = fetch_result.output["raw_bytes"]
            html_text = fetch_result.output["html_text"]

            if fetched.local_path:
                self._record_artifact_to(
                    local_artifacts,
                    artifact_type="raw_document",
                    storage_uri=fetched.local_path,
                    content_type="application/pdf" if fetched.document_type == "pdf" else "application/json",
                    payload=raw_bytes if raw_bytes is not None else html_text,
                    metadata={
                        "document_id": fetched.document_id,
                        "title": fetched.title,
                        "url": fetched.url,
                        "document_type": fetched.document_type,
                    },
                )

            parse_result = parse_tool.run(ctx, document=fetched, raw_bytes=raw_bytes, html_text=html_text)
            if not parse_result.success:
                return

            parsed = parse_result.output
            if fetched.metadata.get("parsed_artifact_uri"):
                self._record_artifact_to(
                    local_artifacts,
                    artifact_type="parsed_document",
                    storage_uri=str(fetched.metadata["parsed_artifact_uri"]),
                    content_type="application/json",
                    payload=parsed.model_dump(mode="json"),
                    metadata={"document_id": fetched.document_id, "title": fetched.title},
                )

            thread_results[index] = {"document": document, "parsed": parsed}
            thread_artifacts[index] = local_artifacts

        n_workers = min(len(documents), 4)
        with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="doc_fetch") as executor:
            futs = {executor.submit(_fetch_parse_one, i, doc): i for i, doc in enumerate(documents)}
            for fut in as_completed(futs):
                fut.result()  # re-raise unexpected thread errors; fetch errors handled inside

        # Merge results in original document order (preserves determinism)
        for result, local_artifacts in zip(thread_results, thread_artifacts):
            if result is None:
                continue
            doc = result["document"]
            parsed = result["parsed"]
            parsed_documents[doc.document_id] = parsed
            all_tables.extend(self.tools.extract_document_tables(parsed))
            artifact_records.extend(local_artifacts)

        # Emit a single completion event from the main thread
        self._emit(progress_callback, "primary_documents", {
            "status": "running",
            "stage_progress": 95,
            "current_substep": f"parsed {len(parsed_documents)}/{len(documents)} documents",
        })

        return parsed_documents, all_tables

    # ── Evidence and sources assembly ────────────────────────────────────────

    def _merge_evidence(
        self,
        plan: ResearchPlan,
        grounded_results: list[GroundedResearchResult],
        documents: list,
        parsed_documents: dict,
    ) -> dict[str, list[dict]]:
        evidence_by_pillar: dict[str, list[dict]] = defaultdict(list)
        for grounded in grounded_results:
            evidence_by_pillar[grounded.pillar_name].extend(grounded.evidence_candidates)
        for workstream in plan.workstreams:
            pillar = workstream.pillar_name or workstream.title
            for document in documents:
                parsed = parsed_documents.get(document.document_id)
                if not parsed:
                    continue
                evidence_by_pillar[pillar].extend(
                    self.tools.extract_document_evidence(pillar, document, parsed)
                )
        return dict(evidence_by_pillar)

    def _build_sources(
        self,
        plan: ResearchPlan,
        grounded_results: list[GroundedResearchResult],
        documents: list,
    ) -> dict[str, list[dict]]:
        sources_by_pillar: dict[str, list[dict]] = defaultdict(list)
        for grounded in grounded_results:
            for source in grounded.sources:
                sources_by_pillar[grounded.pillar_name].append({
                    "title": source.title,
                    "link": source.url,
                    "source_kind": source.source_kind,
                    "is_primary_source": False,
                    "document_type": "html",
                })
        for workstream in plan.workstreams:
            pillar = workstream.pillar_name or workstream.title
            for document in documents:
                sources_by_pillar[pillar].append({
                    "title": document.title,
                    "link": document.url,
                    "source_kind": document.source_kind,
                    "is_primary_source": document.is_primary_source,
                    "document_type": document.document_type,
                    "metadata": document.metadata,
                })
        return dict(sources_by_pillar)

    # ── Artifact persistence ──────────────────────────────────────────────────

    def _persist_doc_index(
        self,
        ticker: str,
        documents: list,
        artifact_records: list,
    ) -> None:
        output = self.memory.artifact_root / ticker.strip().upper() / "documents"
        payload = [d.model_dump(mode="json") if hasattr(d, "model_dump") else d for d in documents]
        if isinstance(self.memory.store, LocalArtifactStore):
            output.mkdir(parents=True, exist_ok=True)
            uri = str(output / "document_index.json")
            (output / "document_index.json").write_text(json.dumps(payload, indent=2))
        else:
            uri = self.memory.store.write_json(
                ticker_artifact_key(ticker, "documents", "document_index.json"), payload
            )
        self._record_artifact_to(
            artifact_records,
            artifact_type="document_index",
            storage_uri=uri,
            content_type="application/json",
            payload=payload,
            metadata={"document_count": len(documents)},
        )

    def _persist_evidence(
        self,
        ticker: str,
        evidence_by_pillar: dict,
        artifact_records: list,
    ) -> None:
        output = self.memory.artifact_root / ticker.strip().upper() / "evidence"
        if isinstance(self.memory.store, LocalArtifactStore):
            output.mkdir(parents=True, exist_ok=True)
            uri = str(output / "document_facts.json")
            (output / "document_facts.json").write_text(json.dumps(evidence_by_pillar, indent=2))
        else:
            uri = self.memory.store.write_json(
                ticker_artifact_key(ticker, "evidence", "document_facts.json"), evidence_by_pillar
            )
        self._record_artifact_to(
            artifact_records,
            artifact_type="document_evidence",
            storage_uri=uri,
            content_type="application/json",
            payload=evidence_by_pillar,
            metadata={"pillar_count": len(evidence_by_pillar)},
        )

    def _record_artifact_to(
        self,
        records: list,
        *,
        artifact_type: str,
        storage_uri: str,
        content_type: str,
        payload: Any,
        metadata: dict | None = None,
    ) -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        backend = getattr(
            self.memory.store, "backend_name",
            "local" if isinstance(self.memory.store, LocalArtifactStore) else "s3"
        )
        records.append({
            "artifact_type": artifact_type,
            "storage_backend": backend,
            "storage_uri": storage_uri,
            "content_type": content_type,
            "size_bytes": len(data),
            "checksum": hashlib.sha256(data).hexdigest(),
            "metadata": metadata or {},
        })

    # ── Summary builder (mirrors CompanyResearchRunner._build_summary) ────────

    def _build_summary(
        self,
        ticker: str,
        company_name: str,
        selected_pillars: list[str],
        grounded_results: list,
        documents: list,
        parsed_documents: dict,
        document_tables: list,
        evidence_by_pillar: dict,
        sources_by_pillar: dict,
        pillar_assessments: dict,
        scorecard: dict,
        artifact_records: list | None = None,
        stage_timings: dict[str, dict[str, Any]] | None = None,
        run_started_at: str = "",
        run_completed_at: str = "",
        total_duration: float = 0.0,
    ) -> dict[str, Any]:
        st = stage_timings or {}

        def _step(stage: str, output_summary: dict[str, Any]) -> dict[str, Any]:
            entry = st.get(stage, {})
            return {
                "stage": stage,
                "status": "completed",
                "started_at": entry.get("started_at", ""),
                "completed_at": entry.get("completed_at", ""),
                "duration_seconds": entry.get("duration_seconds", 0),
                "input_summary": {},
                "output_summary": output_summary,
            }

        stages_present = [
            ("grounded_research", {"grounded_result_count": len(grounded_results)}),
            ("primary_documents", {
                "document_count": len(documents),
                "parsed_document_count": len(parsed_documents),
                "table_count": len(document_tables),
            }),
            ("document_evidence", {"pillar_count": len(evidence_by_pillar)}),
            ("quality_gates", {}),
            ("pillar_assessment", {"pillar_count": len(pillar_assessments)}),
            ("score", {"overall_score": scorecard.get("overall_score", 0)}),
        ]
        steps = [_step(name, out) for name, out in stages_present if name in st]

        return {
            "ticker": ticker,
            "stock_name": company_name,
            "selected_pillars": selected_pillars,
            "query_batch_count": sum(len(r.web_search_queries) for r in grounded_results),
            "primary_source_count": sum(1 for d in documents if d.is_primary_source),
            "filtered_doc_count": len(documents),
            "scraped_doc_count": len(parsed_documents),
            "overall_score": scorecard.get("overall_score", 0),
            "recommendation": scorecard.get("recommendation", "Insufficient Data"),
            "scorecard": scorecard,
            "pillar_assessments": pillar_assessments,
            "evidence_by_pillar": evidence_by_pillar,
            "sources_by_pillar": sources_by_pillar,
            "documents": [d.model_dump(mode="json") if hasattr(d, "model_dump") else d for d in documents],
            "document_tables": document_tables,
            "grounded_results": [r.model_dump(mode="json") for r in grounded_results],
            "artifact_manifest": artifact_records or [],
            "runtime_profile": {
                "stock_name": company_name,
                "ticker": ticker,
                "started_at": run_started_at,
                "completed_at": run_completed_at,
                "total_duration_seconds": total_duration,
                "steps": steps,
                "slowest_steps": sorted(steps, key=lambda s: s["duration_seconds"], reverse=True)[:3],
                "step_share": [],
                "parallelism_candidates": [],
            },
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_ctx(
        self,
        brief: ResearchBrief,
        *,
        ticker: str,
        company_name: str,
        grounded_results: list | None = None,
    ) -> Any:
        from research_core.harness.agent_tools import ToolContext

        return ToolContext(
            brief=brief,
            workstream=self._dummy_workstream(),
            ticker=ticker,
            company_name=company_name,
            grounded_results=grounded_results or [],
        )

    def _dummy_workstream(self) -> Any:
        """Return a minimal workstream when none is in scope (e.g. for doc discovery)."""
        from research_core.harness.models import ResearchWorkstream

        return ResearchWorkstream(id="__loop__", title="Loop", goal="Research loop context")

    def _emit(
        self,
        progress_callback: ProgressCallback | None,
        stage: str,
        payload: dict[str, Any],
    ) -> None:
        if progress_callback:
            progress_callback(stage, payload)


# ── Utility used by gate-replan loop ────────────────────────────────────────

def _plan_with_workstreams(plan: ResearchPlan, workstreams: list) -> ResearchPlan:
    """Return a shallow copy of plan that iterates only over the given workstreams."""

    class _SlicedPlan:
        """Minimal plan-like object for replanned workstream iteration."""

        def __init__(self, original: ResearchPlan, wss: list) -> None:
            self._orig = original
            self.workstreams = wss
            self.stop_conditions = original.stop_conditions

        def add_gate_result(self, gate_result: Any) -> None:
            self._orig.add_gate_result(gate_result)

        def add_observation(self, obs: Any) -> None:
            self._orig.add_observation(obs)

    return _SlicedPlan(plan, workstreams)  # type: ignore[return-value]
