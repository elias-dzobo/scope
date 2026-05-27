"""Compatibility facade for API handlers.

This module preserves existing import paths from the API layer and delegates
business logic to the application service.
"""

from __future__ import annotations

from typing import Any

from scope_api import db
from scope_api.application.run_service import (
    ALL_PILLARS,
    ResearchLimitExceededError,
    ResearchRunRejectedError,
    ResearchRunService,
    service as application_service,
)
from research_core.utils.logger import get_logger

logger = get_logger(__name__)


# Shared application-service instance for API process.
# Keeping this as a module-level singleton preserves current runtime behavior.
run_service: ResearchRunService = application_service


def start_orchestrator() -> None:
    """Start orchestration workers.

    Called from FastAPI startup event.
    """
    run_service.start()


def stop_orchestrator() -> None:
    """Stop orchestration workers.

    Called from FastAPI shutdown event.
    """
    run_service.stop()


def get_orchestration_health() -> dict[str, int]:
    """Expose orchestration health for health endpoints."""
    return run_service.get_run_health()


def get_orchestrator_stats() -> dict[str, int]:
    """Backwards-compatible alias for health metrics."""
    return get_orchestration_health()


def start_research_run(
    company_name: str,
    ticker: str,
    selected_pillars: list[str] | None = None,
    user_id: str | None = None,
) -> str:
    """Create a new research run and enqueue execution."""
    try:
        return run_service.submit_run(
            company_name=company_name.strip(),
            ticker=ticker.strip().upper(),
            selected_pillars=selected_pillars,
            user_id=user_id,
        )
    except (ResearchRunRejectedError, ResearchLimitExceededError) as exc:
        logger.warning("Failed to accept run for ticker=%s: %s", ticker, exc)
        raise RuntimeError(str(exc))


def list_runs(
    limit: int = 20,
    user_id: str | None = None,
    anonymous_only: bool = False,
    status: str | None = None,
    ticker: str | None = None,
    q: str | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List run rows via application service."""
    return run_service.list_runs(
        limit,
        user_id=user_id,
        anonymous_only=anonymous_only,
        status=status,
        ticker=ticker,
        q=q,
        offset=offset,
    )


def get_run(run_id: str, user_id: str | None = None, anonymous_only: bool = False) -> dict[str, Any] | None:
    """Read run row by id."""
    return run_service.get_run(run_id, user_id=user_id, anonymous_only=anonymous_only)


def get_result(run_id: str, user_id: str | None = None, anonymous_only: bool = False) -> dict[str, Any] | None:
    """Read full normalized result payload by run id."""
    dto = run_service.get_result_payload(run_id, user_id=user_id, anonymous_only=anonymous_only)
    if not dto:
        return None

    out: dict[str, Any] = {
        "summary": dto.summary,
        "scorecard": dto.scorecard,
        "pillarAssessments": dto.pillar_assessments,
        "evidenceByPillar": dto.evidence_by_pillar,
        "sourcesByPillar": dto.sources_by_pillar,
        "runtimeProfile": dto.runtime_profile,
        "generatedAt": dto.generated_at,
    }
    if dto.final_synthesis:
        out["finalSynthesis"] = dto.final_synthesis
    if dto.profile_snapshot:
        out["profileSnapshot"] = dto.profile_snapshot
    out["artifacts"] = list_artifacts(run_id, user_id=user_id, anonymous_only=anonymous_only)
    return out


def list_artifacts(run_id: str, user_id: str | None = None, anonymous_only: bool = False) -> list[dict[str, Any]]:
    """List artifact manifest records for one run."""
    return db.list_run_artifacts(run_id, user_id=user_id, anonymous_only=anonymous_only)


__all__ = [
    "ALL_PILLARS",
    "start_orchestrator",
    "stop_orchestrator",
    "get_orchestration_health",
    "get_orchestrator_stats",
    "start_research_run",
    "list_runs",
    "get_run",
    "get_result",
    "list_artifacts",
]
