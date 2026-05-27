"""Prometheus metrics for deterministic and LLM runtime behavior."""

from __future__ import annotations

import os
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Callable

from research_core.utils.logger import get_logger

logger = get_logger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
except Exception:  # pragma: no cover - optional dependency
    Counter = Gauge = Histogram = make_asgi_app = None  # type: ignore

_METRICS_ENABLED = os.getenv("OBSERVABILITY_METRICS_ENABLED", "true").lower() not in {"0", "false", "off", "no"}
_METRICS_APP: Any | None = None


class _NoopMetric:
    """Drop-in no-op metric used when Prometheus client is unavailable."""

    def labels(self, *args: Any, **kwargs: Any) -> "_NoopMetric":
        return self

    def inc(self, amount: float = 1.0) -> None:
        return None

    def set(self, value: float) -> None:
        return None

    def observe(self, value: float) -> None:
        return None


def _metric_or_noop(factory: Callable[..., Any] | None, *args: Any, **kwargs: Any) -> Any:
    if not _METRICS_ENABLED:
        return _NoopMetric()

    if factory is None:
        return _NoopMetric()

    try:
        return factory(*args, **kwargs)
    except Exception as exc:  # pragma: no cover
        logger.warning("Prometheus metric init failed (%s), using no-op: %s", kwargs.get("name"), exc)
        return _NoopMetric()


API_REQUESTS_TOTAL = _metric_or_noop(
    Counter,
    "scope_api_requests_total",
    "Count of API requests processed by method, route, and status.",
    ["method", "route", "status_code"],
)
API_REQUEST_DURATION_SECONDS = _metric_or_noop(
    Histogram,
    "scope_api_request_duration_seconds",
    "Latency of API requests.",
    ["method", "route", "status_code"],
    buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, float("inf")),
)

PIPELINE_STAGE_DURATION_SECONDS = _metric_or_noop(
    Histogram,
    "scope_pipeline_stage_duration_seconds",
    "Latency per pipeline stage.",
    ["stage", "result"],
    buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, float("inf")),
)
PIPELINE_STAGE_TOTAL = _metric_or_noop(
    Counter,
    "scope_pipeline_stage_total",
    "Count of pipeline stages completed or failed.",
    ["stage", "result"],
)

SEARCH_CALL_TOTAL = _metric_or_noop(
    Counter,
    "scope_search_calls_total",
    "Count of web search calls by provider and mode.",
    ["provider", "mode", "result"],
)
SEARCH_CALL_DURATION_SECONDS = _metric_or_noop(
    Histogram,
    "scope_search_call_duration_seconds",
    "Search call latency by provider and mode.",
    ["provider", "mode", "result"],
    buckets=(0.1, 0.2, 0.5, 1, 2, 5, 10, 30, float("inf")),
)
SEARCH_RESULTS = _metric_or_noop(
    Counter,
    "scope_search_results_total",
    "Total search results returned.",
    ["provider", "mode"],
)

LLM_CALL_TOTAL = _metric_or_noop(
    Counter,
    "scope_llm_calls_total",
    "LLM invocation count.",
    ["operation", "provider", "model", "result"],
)
LLM_CALL_DURATION_SECONDS = _metric_or_noop(
    Histogram,
    "scope_llm_call_duration_seconds",
    "LLM invocation latency.",
    ["operation", "provider", "model", "result"],
    buckets=(0.1, 0.3, 0.5, 1, 2, 5, 10, 20, 60, float("inf")),
)
LLM_CALL_TOKENS_TOTAL = _metric_or_noop(
    Counter,
    "scope_llm_tokens_total",
    "LLM token usage.",
    ["provider", "model", "usage_type"],
)

ORCHESTRATOR_SUBMIT_TOTAL = _metric_or_noop(
    Counter,
    "scope_orchestrator_submit_total",
    "Run submissions accepted/rejected by orchestrator.",
    ["outcome"],
)
ORCHESTRATOR_COMPLETION_TOTAL = _metric_or_noop(
    Counter,
    "scope_orchestrator_completion_total",
    "Run completion count.",
    ["result"],
)
ORCHESTRATOR_RETRIES_TOTAL = _metric_or_noop(
    Counter,
    "scope_orchestrator_retries_total",
    "Retry attempts by reason.",
    ["reason"],
)
ORCHESTRATOR_QUEUE_DEPTH = _metric_or_noop(
    Gauge,
    "scope_orchestrator_queue_depth",
    "Current run queue depth.",
)
ORCHESTRATOR_WORKERS = _metric_or_noop(
    Gauge,
    "scope_orchestrator_workers",
    "Active worker count.",
)


def get_metrics_asgi_app() -> Any | None:
    """Return a Prometheus-compatible ASGI app for /metrics."""
    global _METRICS_APP
    if not _METRICS_ENABLED:
        return None
    if _METRICS_APP is not None:
        return _METRICS_APP
    if make_asgi_app is None:
        return None
    _METRICS_APP = make_asgi_app()
    return _METRICS_APP


def record_api_request(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    """Track API request frequency and latency."""
    route_label = route or "unknown"
    labels = {"method": method.upper(), "route": route_label, "status_code": str(status_code)}
    API_REQUESTS_TOTAL.labels(**labels).inc()
    API_REQUEST_DURATION_SECONDS.labels(**labels).observe(max(0.0, duration_seconds))


def record_search_call(
    provider: str,
    mode: str,
    success: bool,
    duration_seconds: float,
    result_count: int,
) -> None:
    """Track search provider usage and fallback behavior."""
    provider = (provider or "unknown").strip().lower()
    mode = (mode or "auto").strip().lower()
    status = "success" if success else "empty"
    SEARCH_CALL_TOTAL.labels(provider=provider, mode=mode, result=status).inc()
    SEARCH_CALL_DURATION_SECONDS.labels(provider=provider, mode=mode, result=status).observe(
        max(0.0, duration_seconds)
    )
    if result_count > 0:
        SEARCH_RESULTS.labels(provider=provider, mode=mode).inc(result_count)


def record_llm_call(
    operation: str,
    provider: str,
    model: str,
    success: bool,
    duration_seconds: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Track LLM call counts, latency and token usage."""
    attrs = {
        "operation": operation,
        "provider": provider,
        "model": model,
        "result": "success" if success else "failure",
    }
    LLM_CALL_TOTAL.labels(**attrs).inc()
    LLM_CALL_DURATION_SECONDS.labels(**attrs).observe(max(0.0, duration_seconds))
    if input_tokens:
        LLM_CALL_TOKENS_TOTAL.labels(provider=provider, model=model, usage_type="input").inc(input_tokens)
    if output_tokens:
        LLM_CALL_TOKENS_TOTAL.labels(provider=provider, model=model, usage_type="output").inc(output_tokens)


def record_pipeline_stage(stage: str, duration_seconds: float, result: str = "success") -> None:
    """Track pipeline stage success/failure and latency."""
    PIPELINE_STAGE_TOTAL.labels(stage=stage, result=result).inc()
    PIPELINE_STAGE_DURATION_SECONDS.labels(stage=stage, result=result).observe(max(0.0, duration_seconds))


def record_orchestrator_submission(outcome: str) -> None:
    """Track orchestrator submit/drop outcomes."""
    ORCHESTRATOR_SUBMIT_TOTAL.labels(outcome=outcome).inc()


def record_orchestrator_completion(result: str) -> None:
    """Track final completion outcomes for pipeline runs."""
    ORCHESTRATOR_COMPLETION_TOTAL.labels(result=result).inc()


def record_orchestrator_retry(reason: str) -> None:
    """Track retry decisions."""
    ORCHESTRATOR_RETRIES_TOTAL.labels(reason=reason).inc()


def record_orchestrator_queue_depth(queued: int, workers: int) -> None:
    """Track queue depth and workers gauge."""
    ORCHESTRATOR_QUEUE_DEPTH.set(float(max(0, int(queued))))
    ORCHESTRATOR_WORKERS.set(float(max(0, int(workers))))


@contextmanager
def observe_pipeline_stage(stage: str):
    """Context manager for pipeline-stage timing metrics."""
    started_at = perf_counter()
    try:
        yield
        result = "success"
    except Exception:
        result = "failure"
        raise
    finally:
        elapsed = perf_counter() - started_at
        record_pipeline_stage(stage=stage, duration_seconds=elapsed, result=result)


def parse_llm_usage(raw_usage: Any) -> tuple[int, int]:
    """Normalize mixed LLM token payload shapes into input/output totals."""
    if not raw_usage:
        return 0, 0
    if isinstance(raw_usage, dict):
        input_tokens = (
            int(raw_usage.get("prompt_tokens", 0))
            or int(raw_usage.get("input_tokens", 0))
            or int(raw_usage.get("prompt", 0))
            or 0
        )
        output_tokens = (
            int(raw_usage.get("completion_tokens", 0))
            or int(raw_usage.get("output_tokens", 0))
            or int(raw_usage.get("completion", 0))
            or 0
        )
        return input_tokens, output_tokens
    return 0, 0
