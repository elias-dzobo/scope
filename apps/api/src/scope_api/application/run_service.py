"""Application service layer for stock research orchestration.

This module contains the core domain workflows.
It is intentionally transport-agnostic and does not import FastAPI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from scope_api import db
from scope_api.memory import index_completed_research_run
from scope_api.orchestration import OrchestrationManager, OrchestratorConfig
from research_core.harness.controller import ResearchController
from scope_api.observability.metrics import (
    observe_pipeline_stage,
    record_orchestrator_completion,
    record_orchestrator_submission,
)
from scope_api.observability.telemetry import observe_span
from research_core.storage import artifact_store
from research_core.utils.logger import get_logger

logger = get_logger(__name__)

ALL_PILLARS = [
    "Macro & Industry",
    "Economic Moat",
    "Financial Engine",
    "Management & Capital Allocation",
    "Valuation",
    "Technical Analysis",
]

DEFAULT_DURABLE_ARTIFACT_TYPES = {
    "final_synthesis",
    "document_evidence",
}

STAGE_ORDER = [
    "01_prepare",
    "planning",
    "grounded_research",
    "primary_documents",
    "document_evidence",
    "quality_gates",
    "targeted_fallback",
    "pillar_assessment",
    "score",
    "final_synthesis",
]
STAGE_PROGRESS = {stage: ((idx + 1) / len(STAGE_ORDER)) * 100 for idx, stage in enumerate(STAGE_ORDER)}
STAGE_START_PROGRESS = {stage: (idx / len(STAGE_ORDER)) * 100 for idx, stage in enumerate(STAGE_ORDER)}


class ResearchRunRejectedError(RuntimeError):
    """Raised when run submission cannot be accepted by the orchestrator."""


class ResearchLimitExceededError(RuntimeError):
    """Raised when a user exceeds configured research limits."""


@dataclass(frozen=True)
class RunArtifacts:
    """Normalized payload persisted as run result."""

    summary: dict[str, Any]
    scorecard: dict[str, Any]
    pillar_assessments: dict[str, Any]
    evidence_by_pillar: dict[str, Any]
    sources_by_pillar: dict[str, Any]
    runtime_profile: dict[str, Any]
    generated_at: str
    final_synthesis: dict[str, Any] | None = None
    profile_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True)
class OrchestratorConfigInput:
    """Container for orchestrator config extracted from environment."""

    max_workers: int
    max_queue_depth: int
    max_retries: int
    retry_base_seconds: float
    worker_name_prefix: str = "scope-worker"

    @classmethod
    def from_env(cls) -> "OrchestratorConfigInput":
        """Read orchestrator tuning values from environment."""

        return cls(
            max_workers=_to_int(os.getenv("RESEARCH_MAX_WORKERS", "2"), 2),
            max_queue_depth=_to_int(os.getenv("RESEARCH_QUEUE_DEPTH", "200"), 200),
            max_retries=_to_int(os.getenv("RESEARCH_MAX_RETRIES", "1"), 1),
            retry_base_seconds=_to_float(os.getenv("RESEARCH_RETRY_BASE_SECONDS", "2") or "2", 2.0),
            worker_name_prefix="scope-worker",
        )

    def to_orchestrator_config(self) -> OrchestratorConfig:
        """Convert to the infra-layer config object."""
        return OrchestratorConfig(
            max_workers=self.max_workers,
            max_queue_depth=self.max_queue_depth,
            max_retries=self.max_retries,
            retry_base_seconds=self.retry_base_seconds,
            worker_name_prefix=self.worker_name_prefix,
        )


def execution_backend() -> str:
    """Return the configured research execution backend.

    ``in_process`` preserves local compatibility. ``durable`` stores queued
    jobs in the database for a separate worker process to lease and execute.
    """
    return os.getenv("RESEARCH_EXECUTION_BACKEND", "in_process").strip().lower() or "in_process"


def _to_int(value: str, default: int) -> int:
    """Convert env var string to int with safe fallback."""
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _to_float(value: str, default: float) -> float:
    """Convert env var string to float with safe fallback."""
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


def _daily_window_start() -> str:
    """Return the UTC timestamp for the current daily quota window."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _build_result_payload(summary: dict[str, Any]) -> dict[str, Any]:
    """Project stock pipeline output into a stable API result contract."""
    payload: dict[str, Any] = {
        "summary": {
            "ticker": summary["ticker"],
            "stock_name": summary["stock_name"],
            "query_batch_count": summary["query_batch_count"],
            "filtered_doc_count": summary["filtered_doc_count"],
            "scraped_doc_count": summary["scraped_doc_count"],
            "overall_score": summary["overall_score"],
            "recommendation": summary["recommendation"],
        },
        "scorecard": summary["scorecard"],
        "pillarAssessments": summary["pillar_assessments"],
        "evidenceByPillar": summary["evidence_by_pillar"],
        "sourcesByPillar": summary["sources_by_pillar"],
        "runtimeProfile": summary.get("runtime_profile", {}),
        "generatedAt": db.utc_now_iso(),
    }
    fs = summary.get("final_synthesis")
    if fs:
        payload["finalSynthesis"] = fs
    ps = summary.get("profile_snapshot")
    if ps:
        payload["profileSnapshot"] = ps
    return payload


def _artifact_to_dto(payload: dict[str, Any]) -> RunArtifacts:
    """Convert persisted run payload to immutable DTO.

    Backward compatible with both modern and legacy persisted result shapes.
    """
    return RunArtifacts(
        summary=payload.get("summary", {}),
        scorecard=payload.get("scorecard", payload.get("scoreCard", {})),
        pillar_assessments=payload.get(
            "pillarAssessments",
            payload.get("pillar_assessments", {}),
        ),
        evidence_by_pillar=payload.get(
            "evidenceByPillar",
            payload.get("evidence_by_pillar", {}),
        ),
        sources_by_pillar=payload.get(
            "sourcesByPillar",
            payload.get("sources_by_pillar", {}),
        ),
        runtime_profile=payload.get(
            "runtimeProfile",
            payload.get("runtime_profile", {}),
        ),
        generated_at=payload.get("generatedAt", payload.get("generated_at", "")),
        final_synthesis=payload.get("finalSynthesis") or payload.get("final_synthesis"),
        profile_snapshot=payload.get("profileSnapshot") or payload.get("profile_snapshot"),
    )


class ResearchRunService:
    """Application service for research-run use-cases.

    The service is designed for testability:
    - orchestration can be swapped via dependency injection
    - persistence remains behind the `src.api.db` adapter
    - pipeline invocation is centralized and instrumented with progress callbacks
    """

    def __init__(self, orchestrator: OrchestrationManager | None = None) -> None:
        self._orchestrator = orchestrator or OrchestrationManager(
            runner=self._run_pipeline_job,
            on_job_failed=self._on_job_failed,
            config=OrchestratorConfigInput.from_env().to_orchestrator_config(),
        )
        self._research_controller = ResearchController()

    def start(self) -> None:
        """Start background worker pool."""
        self._orchestrator.start()

    def stop(self) -> None:
        """Stop background worker pool."""
        self._orchestrator.shutdown()

    def submit_run(
        self,
        company_name: str,
        ticker: str,
        selected_pillars: list[str] | None = None,
        user_id: str | None = None,
    ) -> str:
        """Create a run record and submit work to the orchestration backend.

        Returns run_id immediately. Raises `ResearchRunRejectedError` when queue is full.
        """
        normalized_pillars = [p.strip() for p in (selected_pillars or ALL_PILLARS)]
        self._enforce_research_limits(user_id)

        run_id = self._create_run_record(
            company_name=company_name,
            ticker=ticker,
            selected_pillars=normalized_pillars,
            user_id=user_id,
        )

        if execution_backend() == "durable":
            db.append_event(
                run_id,
                "queued",
                "queued",
                {
                    "current_substep": "waiting for research worker",
                    "executionBackend": "durable",
                },
            )
            record_orchestrator_submission("accepted_durable")
            return run_id

        accepted = self._orchestrator.submit(
            run_id=run_id,
            company_name=company_name,
            ticker=ticker,
            selected_pillars=normalized_pillars,
        )

        if not accepted:
            record_orchestrator_submission("rejected_queue_full")
            db.update_run_state(
                run_id,
                status="failed",
                current_stage="rejected",
                progress=0,
                error_message="Submission rejected: orchestration queue is full",
                completed=True,
            )
            raise ResearchRunRejectedError("Orchestration queue is full")

        record_orchestrator_submission("accepted")
        return run_id

    def _enforce_research_limits(self, user_id: str | None) -> None:
        """Apply lightweight per-user abuse controls before queue submission."""
        if not user_id:
            return
        daily_limit = _to_int(os.getenv("RESEARCH_DAILY_LIMIT_PER_USER", "10"), 10)
        active_limit = _to_int(os.getenv("RESEARCH_MAX_ACTIVE_RUNS_PER_USER", "2"), 2)
        if daily_limit > 0 and db.count_user_runs_since(user_id, _daily_window_start()) >= daily_limit:
            raise ResearchLimitExceededError("Daily research limit reached. Please try again tomorrow.")
        if active_limit > 0 and db.count_active_user_runs(user_id) >= active_limit:
            raise ResearchLimitExceededError("You already have the maximum number of active research runs.")

    def get_run(
        self,
        run_id: str,
        user_id: str | None = None,
        anonymous_only: bool = False,
    ) -> dict[str, Any] | None:
        """Load run row by id."""
        return db.get_run(run_id, user_id=user_id, anonymous_only=anonymous_only)

    def list_runs(
        self,
        limit: int,
        user_id: str | None = None,
        anonymous_only: bool = False,
        status: str | None = None,
        ticker: str | None = None,
        q: str | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List most recent runs by created time."""
        return db.list_runs(
            limit=limit,
            user_id=user_id,
            anonymous_only=anonymous_only,
            status=status,
            ticker=ticker,
            q=q,
            offset=offset,
        )

    def get_result_payload(
        self,
        run_id: str,
        user_id: str | None = None,
        anonymous_only: bool = False,
    ) -> RunArtifacts | None:
        """Load and normalize run result payload.

        Returns None when run does not exist.
        """
        row = db.get_run(run_id, user_id=user_id, anonymous_only=anonymous_only)
        if not row or not row.get("result"):
            return None
        return _artifact_to_dto(row["result"])

    def get_run_health(self) -> dict[str, int]:
        """Expose orchestration metrics to API and operations."""
        return self._orchestrator.get_health()

    def _on_job_failed(self, run_id: str, exc: BaseException, attempt: int) -> None:
        """Persist terminal failure and mark status in DB."""
        record_orchestrator_completion("failed")
        db.update_run_state(
            run_id,
            status="failed",
            current_stage="failed",
            progress=100,
            error_message=f"{str(exc)} | attempts={attempt + 1}",
            completed=True,
        )

    def _create_run_record(
        self,
        company_name: str,
        ticker: str,
        selected_pillars: list[str],
        user_id: str | None = None,
    ) -> str:
        """Persist run metadata and return new run id."""
        from uuid import uuid4

        run_id = str(uuid4())
        db.create_run(
            run_id,
            company_name=company_name,
            ticker=ticker,
            selected_pillars=selected_pillars,
            user_id=user_id,
            max_retries=OrchestratorConfigInput.from_env().max_retries,
            budget_snapshot=self._build_budget_snapshot(user_id=user_id, selected_pillars=selected_pillars),
        )
        return run_id

    def _build_budget_snapshot(self, *, user_id: str | None, selected_pillars: list[str]) -> dict[str, Any]:
        """Capture the run budget used for abuse control, billing, and audit.

        This is intentionally estimate-based for now. The pricing ledger can
        later reserve credits from this same snapshot before a durable worker
        begins execution.
        """
        pillar_count = max(len(selected_pillars), 1)
        return {
            "pricingModel": os.getenv("SCOPE_PRICING_MODEL", "hybrid_subscription_credits"),
            "estimatedCredits": float(os.getenv("RESEARCH_COMPANY_BASE_CREDITS", "5")) + pillar_count,
            "providerCallBudget": _to_int(os.getenv("RESEARCH_PROVIDER_CALL_BUDGET", "80"), 80),
            "tokenBudget": _to_int(os.getenv("RESEARCH_TOKEN_BUDGET", "150000"), 150000),
            "documentBudget": _to_int(os.getenv("RESEARCH_DOCUMENT_BUDGET", "25"), 25),
            "retryBudget": OrchestratorConfigInput.from_env().max_retries,
            "userId": user_id or "",
            "capturedAt": db.utc_now_iso(),
        }

    def _run_pipeline_job(self, run_id: str, company_name: str, ticker: str, selected_pillars: list[str]) -> None:
        """Synchronous pipeline execution function for one queue job."""
        with observe_span(
            "service.run_pipeline_job",
            {"run_id": run_id, "ticker": ticker, "company_name": company_name},
        ):
            db.update_run_state(
                run_id,
                status="running",
                current_stage="01_prepare",
                current_substep="initializing run",
                progress=1,
                stage_progress=0,
                activity_count=1,
                error_message="",
                last_activity_at=db.utc_now_iso(),
                started_at=db.utc_now_iso(),
            )

            def on_progress(stage: str, payload: dict[str, Any]) -> None:
                status = payload.get("status", "running")
                stage_start = STAGE_START_PROGRESS.get(stage, 0.0)
                stage_end = STAGE_PROGRESS.get(stage, 0.0)
                stage_span = max(stage_end - stage_start, 0.0)
                stage_progress = float(payload.get("stage_progress", 0.0) or 0.0)
                progress = stage_end if status == "completed" else stage_start + (stage_span * (stage_progress / 100.0))
                current_substep = str(payload.get("current_substep", "") or "")
                current = db.get_run(run_id)
                activity_count = int(current.get("activity_count", 0) or 0) + 1 if current else 1
                db.append_event(run_id, stage, status, payload)
                db.update_run_state(
                    run_id,
                    current_stage=stage,
                    current_substep=current_substep,
                    progress=max(progress, 1),
                    stage_progress=100 if status == "completed" else stage_progress,
                    activity_count=activity_count,
                    last_activity_at=db.utc_now_iso(),
                )

            with observe_pipeline_stage("service_orchestration"):
                run_row = db.get_run(run_id)
                profile_snapshot = run_row.get("profile_snapshot") if run_row else None
                summary = self._research_controller.run_company_research(
                    company_name=company_name,
                    ticker=ticker,
                    selected_pillars=selected_pillars,
                    progress_callback=on_progress,
                    user_financial_profile=profile_snapshot.get("financialProfile") if profile_snapshot else None,
                    user_risk_profile=profile_snapshot.get("riskProfile") if profile_snapshot else None,
                    investor_context=profile_snapshot.get("investorContext") if profile_snapshot else None,
                    run_id=run_id,
                    user_id=run_row.get("user_id") if run_row else None,
                )
                if profile_snapshot:
                    summary["profile_snapshot"] = profile_snapshot

            # ── Phase 0.1: detect synthesis failure and persist partial ──────
            synthesis_failed = bool(summary.pop("synthesis_failed", False))
            synthesis_error = str(summary.pop("synthesis_error", ""))
            if synthesis_failed:
                logger.warning(
                    "Synthesis failed — persisting partial result | run_id=%s | error=%s",
                    run_id,
                    synthesis_error,
                )
                db.append_event(
                    run_id,
                    "synthesis_failed",
                    "failed",
                    {"error": synthesis_error, "note": "scorecard and evidence are available"},
                )

            result_payload = _build_result_payload(summary)
            artifact_records = self._apply_artifact_retention(
                run_id=run_id,
                artifacts=summary.get("artifact_manifest", []),
            )
            self._register_artifacts(
                run_id=run_id,
                user_id=run_row.get("user_id") if run_row else "",
                ticker=ticker,
                artifacts=artifact_records,
            )
            final_status = "completed_partial" if synthesis_failed else "completed"
            db.update_run_state(
                run_id,
                status=final_status,
                current_stage=final_status,
                current_substep=(
                    "run finished — synthesis unavailable, scorecard ready"
                    if synthesis_failed
                    else "run finished"
                ),
                progress=100,
                stage_progress=100,
                summary=result_payload["summary"],
                result=result_payload,
                last_activity_at=db.utc_now_iso(),
                completed=True,
            )
            self._index_user_memory(run_id=run_id, result_payload=result_payload)
            record_orchestrator_completion("partial" if synthesis_failed else "success")

    def _register_artifacts(
        self,
        *,
        run_id: str,
        user_id: str | None,
        ticker: str,
        artifacts: list[dict[str, Any]],
    ) -> None:
        """Persist manifest entries emitted by the research runner."""
        for artifact in artifacts:
            db.create_artifact_record(
                run_id=run_id,
                user_id=user_id,
                ticker=ticker,
                artifact_type=str(artifact.get("artifact_type", "")),
                storage_backend=str(artifact.get("storage_backend", "")),
                storage_uri=str(artifact.get("storage_uri", "")),
                content_type=str(artifact.get("content_type", "application/octet-stream")),
                size_bytes=int(artifact.get("size_bytes", 0) or 0),
                checksum=str(artifact.get("checksum", "")),
                metadata=dict(artifact.get("metadata") or {}),
            )

    def _apply_artifact_retention(self, *, run_id: str, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Delete temporary research artifacts and return durable records.

        The final research result carries the user-facing synthesis, evidence
        facts, sources, and profile snapshot. Raw PDFs, parsed documents, table
        JSON, and trace-like working files are useful during execution but are
        not needed permanently for the product experience.
        """
        mode = os.getenv("ARTIFACT_RETENTION_MODE", "ephemeral").strip().lower() or "ephemeral"
        if mode in {"retain_all", "debug"}:
            return artifacts

        keep_types = _artifact_keep_types()
        store = artifact_store()
        kept: list[dict[str, Any]] = []
        deleted = 0
        delete_failed = 0
        for artifact in artifacts:
            artifact_type = str(artifact.get("artifact_type", ""))
            if artifact_type in keep_types:
                kept.append(artifact)
                continue
            uri = str(artifact.get("storage_uri", ""))
            if uri and store.delete_uri(uri):
                deleted += 1
            else:
                delete_failed += 1
                logger.debug("Skipped temporary artifact deletion | run_id=%s | type=%s | uri=%s", run_id, artifact_type, uri)
        db.append_event(
            run_id,
            "artifact_retention",
            "completed",
            {
                "mode": mode,
                "kept": len(kept),
                "deleted": deleted,
                "deleteFailed": delete_failed,
                "keptTypes": sorted(keep_types),
            },
        )
        return kept

    def _index_user_memory(self, *, run_id: str, result_payload: dict[str, Any]) -> None:
        """Best-effort indexing of completed runs into user memory.

        Memory should improve personalization, but a graph indexing failure must
        not turn a finished research report into a failed run.
        """
        run = db.get_run(run_id)
        if not run or not run.get("user_id"):
            return
        try:
            artifacts = db.list_run_artifacts(run_id, user_id=run["user_id"])
            stats = index_completed_research_run(run=run, result_payload=result_payload, artifacts=artifacts)
            db.append_event(run_id, "memory_index", "completed", stats)
        except Exception as exc:  # pragma: no cover - defensive production guard
            logger.warning("Failed to index user memory | run_id=%s | error=%s", run_id, exc)
            db.append_event(run_id, "memory_index", "failed", {"error": str(exc)})


# Shared service instance used by API-facing module.
service = ResearchRunService()


def _artifact_keep_types() -> set[str]:
    """Return durable artifact types from env or the product default."""
    raw = os.getenv("ARTIFACT_KEEP_TYPES", "").strip()
    keep_types = set(DEFAULT_DURABLE_ARTIFACT_TYPES) if not raw else {item.strip() for item in raw.split(",") if item.strip()}
    if os.getenv("ARTIFACT_KEEP_RAW_DOCUMENTS", "false").strip().lower() == "true":
        keep_types.add("raw_document")
    return keep_types
