"""FastAPI app exposing stock research pipeline endpoints.

This module is the API entrypoint for the service.
Its responsibilities are intentionally narrow:
- Validate and parse inbound payloads
- Enforce auth, throttling, and correlation id headers
- Trigger service-level orchestration
- Return standardized envelope responses on the versioned API
"""

from __future__ import annotations

import os
from alembic import command
from alembic.config import Config
from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from scope_api import db
from scope_api.advisor import build_advisor_router
from scope_api.auth.dependencies import optional_current_user
from scope_api.auth.models import AuthUser
from scope_api.auth.routes import build_auth_router
from scope_api.config import ApiConfig, load_api_config
from scope_api.dependencies import get_request_id, require_api_key
from scope_api.middleware import (
    CorrelationIdMiddleware,
    ObservabilityMiddleware,
    RequestBodySizeLimitMiddleware,
    SlidingWindowRateLimitMiddleware,
)
from scope_api.memory import build_memory_router
from scope_api.schemas import (
    ApiEnvelope,
    RunListResponse,
    RunResultResponse,
    RunStatusResponse,
    StartResearchRunRequest,
    StartResearchRunResponse,
)
from scope_api.service import (
    get_orchestrator_stats,
    get_run,
    get_result,
    list_artifacts,
    list_runs,
    start_orchestrator,
    start_research_run,
    stop_orchestrator,
)
from scope_api.observability.metrics import get_metrics_asgi_app
from scope_api.observability.telemetry import init_observability
from scope_api.onboarding import build_onboarding_router
from research_core.utils.logger import configure_logging
from research_core.utils.logger import get_logger

logger = get_logger(__name__)


def _to_envelope(value, request_id: str) -> dict[str, object]:
    """Build a standard response envelope for versioned endpoints."""
    return {"request_id": request_id, "data": value}


def _normalize_limit(limit: int, config: ApiConfig) -> int:
    """Guard list endpoints from pathological page sizes."""
    return max(1, min(limit, config.max_limit))


def _current_user_id(user: AuthUser | object | None) -> str | None:
    """Return a user id only when FastAPI supplied an authenticated user."""
    return user.id if isinstance(user, AuthUser) else None


def _require_user_when_configured(user: AuthUser | None, config: ApiConfig) -> None:
    """Enforce authenticated ownership on user data routes in production."""
    if config.require_auth and user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


def _build_router(config: ApiConfig) -> APIRouter:
    """Build versioned v1 routes.

    Using a router keeps endpoint registration clean and allows introducing v2 later
    without touching orchestration logic.
    """
    router = APIRouter(prefix=config.api_v1_prefix, tags=["research-runs"])

    @router.post(
        "/research-runs",
        response_model=ApiEnvelope[StartResearchRunResponse],
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_research_run_v1(
        payload: StartResearchRunRequest,
        request_id: str = Depends(get_request_id),
        _api_key: str | None = Depends(require_api_key),
        current_user: AuthUser | None = Depends(optional_current_user),
    ):
        """Create an asynchronous research run.

        Orchestration itself runs via the service-backed background workers.
        """
        _require_user_when_configured(current_user, config)
        try:
            run_id = start_research_run(
                company_name=payload.company_name.strip(),
                ticker=payload.ticker.strip().upper(),
                selected_pillars=payload.selected_pillars,
                user_id=_current_user_id(current_user),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        logger.info(
            "Queued research run | run_id=%s | ticker=%s | request_id=%s",
            run_id,
            payload.ticker,
            request_id,
        )
        response = StartResearchRunResponse(run_id=run_id, status="queued")
        return _to_envelope(response.model_dump(), request_id)

    @router.get("/research-runs", response_model=ApiEnvelope[RunListResponse])
    async def list_research_runs_v1(
        request_id: str = Depends(get_request_id),
        _api_key: str | None = Depends(require_api_key),
        current_user: AuthUser | None = Depends(optional_current_user),
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        ticker: str | None = None,
        q: str | None = None,
    ):
        """List recent runs with bounded pagination."""
        _require_user_when_configured(current_user, config)
        normalized_limit = _normalize_limit(limit, config)
        user_id = _current_user_id(current_user)
        runs = list_runs(
            limit=normalized_limit,
            user_id=user_id,
            anonymous_only=user_id is None,
            status=status,
            ticker=ticker,
            q=q,
            offset=offset,
        )
        response = RunListResponse(
            items=[RunStatusResponse(**run) for run in runs],
            total=len(runs),
        )
        return _to_envelope(response.model_dump(), request_id)

    @router.get("/research-runs/{run_id}", response_model=ApiEnvelope[RunStatusResponse])
    async def get_research_run_v1(
        run_id: str,
        request_id: str = Depends(get_request_id),
        _api_key: str | None = Depends(require_api_key),
        current_user: AuthUser | None = Depends(optional_current_user),
    ):
        """Get status/progress for a single run."""
        _require_user_when_configured(current_user, config)
        user_id = _current_user_id(current_user)
        run = get_run(run_id, user_id=user_id, anonymous_only=user_id is None)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return _to_envelope(RunStatusResponse(**run).model_dump(), request_id)

    @router.get("/research-runs/{run_id}/results", response_model=ApiEnvelope[RunResultResponse])
    async def get_research_run_results_v1(
        run_id: str,
        request_id: str = Depends(get_request_id),
        _api_key: str | None = Depends(require_api_key),
        current_user: AuthUser | None = Depends(optional_current_user),
    ):
        """Get final result payload for a completed run."""
        _require_user_when_configured(current_user, config)
        user_id = _current_user_id(current_user)
        run = get_run(run_id, user_id=user_id, anonymous_only=user_id is None)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        payload = RunResultResponse(
            id=run["id"],
            status=run["status"],
            result=get_result(run_id, user_id=user_id, anonymous_only=user_id is None),
        )
        return _to_envelope(payload.model_dump(), request_id)

    @router.get("/research-runs/{run_id}/artifacts")
    async def get_research_run_artifacts_v1(
        run_id: str,
        request_id: str = Depends(get_request_id),
        _api_key: str | None = Depends(require_api_key),
        current_user: AuthUser | None = Depends(optional_current_user),
    ):
        """List stored artifacts owned by a research run."""
        _require_user_when_configured(current_user, config)
        user_id = _current_user_id(current_user)
        run = get_run(run_id, user_id=user_id, anonymous_only=user_id is None)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        items = list_artifacts(run_id, user_id=user_id, anonymous_only=user_id is None)
        return _to_envelope(
            {
                "items": items,
                "total": len(items),
            },
            request_id,
        )

    @router.get("/health")
    async def health_v1() -> dict[str, object]:
        """Liveness endpoint for platform checks."""
        return {
            "status": "ok",
            "version": "0.1.0",
            "layer": "v1",
            "orchestrator": get_orchestrator_stats(),
        }

    return router


def _run_startup_migrations() -> None:
    """Run Alembic migrations when explicitly enabled."""
    if os.getenv("SCOPE_AUTO_MIGRATE", "false").lower() != "true":
        return
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
    command.upgrade(Config(os.path.join(root, "alembic.ini")), "head")


app = FastAPI(title="Scope Stock Research API", version="0.1.0")
configure_logging()
config = load_api_config()
app.state.config = config
init_observability(app=app, service_name=os.getenv("OTEL_SERVICE_NAME", "scope-api"), service_version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(CorrelationIdMiddleware, header_name=config.request_id_header)
app.add_middleware(RequestBodySizeLimitMiddleware, config=config)
app.add_middleware(SlidingWindowRateLimitMiddleware, config=config)
app.include_router(build_auth_router(config.api_v1_prefix))
app.include_router(build_onboarding_router(config.api_v1_prefix))
app.include_router(build_memory_router(config.api_v1_prefix))
app.include_router(build_advisor_router(config.api_v1_prefix))
app.include_router(_build_router(config))

metrics_app = get_metrics_asgi_app()
if metrics_app is not None:
    app.mount("/metrics", metrics_app)


@app.on_event("startup")
def _startup() -> None:
    """Initialize persistence and start orchestration workers."""
    _run_startup_migrations()
    db.init_db()
    start_orchestrator()


@app.on_event("shutdown")
def _shutdown() -> None:
    """Stop orchestration workers cleanly."""
    stop_orchestrator()


@app.get("/health")
def health() -> dict[str, object]:
    """Legacy compatibility health endpoint."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "layer": "legacy",
        "orchestrator": get_orchestrator_stats(),
    }


# Backward-compatible route handlers for existing integrations.
@app.post("/research-runs", response_model=StartResearchRunResponse)
def create_research_run(
    payload: StartResearchRunRequest,
    _api_key: str | None = Depends(require_api_key),
    current_user: AuthUser | None = Depends(optional_current_user),
) -> StartResearchRunResponse:
    """Create run (legacy sync path).

    Keeps pre-v1 integrations intact during migration.
    """
    _require_user_when_configured(current_user, config)
    try:
        run_id = start_research_run(
            company_name=payload.company_name.strip(),
            ticker=payload.ticker.strip().upper(),
            selected_pillars=payload.selected_pillars,
            user_id=_current_user_id(current_user),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return StartResearchRunResponse(run_id=run_id, status="queued")


@app.get("/research-runs", response_model=list[RunStatusResponse])
def list_research_runs(
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    ticker: str | None = None,
    q: str | None = None,
    _api_key: str | None = Depends(require_api_key),
    current_user: AuthUser | None = Depends(optional_current_user),
) -> list[RunStatusResponse]:
    """List runs (legacy sync path)."""
    _require_user_when_configured(current_user, config)
    user_id = _current_user_id(current_user)
    runs = list_runs(
        limit=limit,
        user_id=user_id,
        anonymous_only=user_id is None,
        status=status,
        ticker=ticker,
        q=q,
        offset=offset,
    )
    return [RunStatusResponse(**run) for run in runs]


@app.get("/research-runs/{run_id}", response_model=RunStatusResponse)
def get_research_run(
    run_id: str,
    _api_key: str | None = Depends(require_api_key),
    current_user: AuthUser | None = Depends(optional_current_user),
) -> RunStatusResponse:
    """Get single run (legacy sync path)."""
    _require_user_when_configured(current_user, config)
    user_id = _current_user_id(current_user)
    run = get_run(run_id, user_id=user_id, anonymous_only=user_id is None)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunStatusResponse(**run)


@app.get("/research-runs/{run_id}/results", response_model=RunResultResponse)
def get_research_run_results(
    run_id: str,
    _api_key: str | None = Depends(require_api_key),
    current_user: AuthUser | None = Depends(optional_current_user),
) -> RunResultResponse:
    """Get run results (legacy sync path)."""
    _require_user_when_configured(current_user, config)
    user_id = _current_user_id(current_user)
    run = get_run(run_id, user_id=user_id, anonymous_only=user_id is None)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    return RunResultResponse(
        id=run["id"],
        status=run["status"],
        result=get_result(run_id, user_id=user_id, anonymous_only=user_id is None),
    )


@app.get("/research-runs/{run_id}/artifacts")
def get_research_run_artifacts(
    run_id: str,
    _api_key: str | None = Depends(require_api_key),
    current_user: AuthUser | None = Depends(optional_current_user),
) -> dict[str, object]:
    """List artifacts for a run (legacy sync path)."""
    _require_user_when_configured(current_user, config)
    user_id = _current_user_id(current_user)
    run = get_run(run_id, user_id=user_id, anonymous_only=user_id is None)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    items = list_artifacts(run_id, user_id=user_id, anonymous_only=user_id is None)
    return {"items": items, "total": len(items)}
