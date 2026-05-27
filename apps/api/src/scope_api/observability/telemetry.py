"""OpenTelemetry bootstrap and span utilities for service-wide tracing."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Mapping, Sequence

from research_core.utils.logger import get_logger

logger = get_logger(__name__)

_OBSERVER_INITIALIZED = False
_TRACER: Any | None = None


def _env_truthy(name: str, default: bool = False) -> bool:
    """Read environment flags with tolerant defaults."""
    raw = os.getenv(name, "").strip().lower()
    if raw == "":
        return default
    return raw not in {"0", "false", "f", "no", "n", "off"}


def _sampling_ratio() -> float:
    try:
        return min(1.0, max(0.0, float(os.getenv("OTEL_SAMPLING_RATIO", "1.0").strip())))
    except (TypeError, ValueError):
        return 1.0


def init_observability(
    app: Any | None = None,
    service_name: str | None = None,
    service_version: str | None = None,
) -> Any | None:
    """Initialize OpenTelemetry tracing.

    Returns the created tracer when initialization succeeds, otherwise ``None``.
    """
    global _OBSERVER_INITIALIZED, _TRACER

    if _OBSERVER_INITIALIZED:
        return _TRACER
    _OBSERVER_INITIALIZED = True

    if not _env_truthy("OBSERVABILITY_ENABLED", default=False):
        logger.info("OpenTelemetry disabled by OBSERVABILITY_ENABLED=false")
        return None

    if not _env_truthy("OTEL_TRACES_ENABLED", default=True):
        logger.info("OpenTelemetry traces disabled by OTEL_TRACES_ENABLED=false")
        return None

    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except Exception as exc:  # pragma: no cover - missing optional dependency path
        logger.warning("OpenTelemetry is unavailable: %s", exc)
        return None

    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
    except Exception:  # pragma: no cover - optional instrumentation path
        LoggingInstrumentor = None

    service_name = (service_name or os.getenv("OTEL_SERVICE_NAME", "scope-api")).strip() or "scope-api"
    service_version = (service_version or os.getenv("OTEL_SERVICE_VERSION", "0.1.0")).strip() or "0.1.0"
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317").strip()
    insecure = _env_truthy("OTEL_EXPORTER_OTLP_INSECURE", default=True)

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.version": service_version,
            }
        ),
        sampler=ParentBased(root=TraceIdRatioBased(_sampling_ratio())),
    )

    try:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=insecure)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer(__name__)
    except Exception as exc:
        logger.exception("Failed to configure OTLP span exporter; tracing disabled: %s", exc)
        _TRACER = trace.get_tracer(__name__)

    if app is not None:
        try:
            FastAPIInstrumentor().instrument_app(app)
        except Exception as exc:  # pragma: no cover
            logger.warning("FastAPI OpenTelemetry instrumentation skipped: %s", exc)

    try:
        RequestsInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        logger.debug("Request instrumentation skipped: %s", exc)

    if LoggingInstrumentor is not None:
        try:
            LoggingInstrumentor().instrument(set_logging_format=True)
        except Exception as exc:  # pragma: no cover
            logger.debug("Logging instrumentation skipped: %s", exc)

    logger.info(
        "OpenTelemetry initialized | service=%s version=%s otlp=%s",
        service_name,
        service_version,
        otlp_endpoint,
    )
    return _TRACER


@contextmanager
def observe_span(name: str, attributes: Mapping[str, Any] | None = None):
    """Create an OpenTelemetry span with standard error handling.

    If tracing is disabled or unavailable, this becomes a no-op context manager.
    """
    if _TRACER is None:
        yield None
        return

    with _TRACER.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                if value is None:
                    continue
                try:
                    span.set_attribute(f"{key}", value)
                except Exception:
                    continue
        try:
            yield span
        except Exception as exc:
            _record_span_error(span, exc)
            raise


def _record_span_error(span: Any, exc: Exception) -> None:
    """Attach exception metadata onto the active span."""
    try:
        span.record_exception(exc)
    except Exception:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, str(exc)))
    except Exception:
        span.set_status("ERROR")  # type: ignore[call-arg]


def set_span_attributes(attributes: Mapping[str, Any] | Sequence[tuple[str, Any]]) -> None:
    """Attach attributes to the current active span when tracing is active."""
    if not attributes:
        return

    try:
        from opentelemetry import trace
    except Exception:
        return

    span = trace.get_current_span()
    if span is None:
        return

    if isinstance(attributes, dict):
        items = attributes.items()
    else:
        items = attributes

    for key, value in items:
        if value is None:
            continue
        try:
            span.set_attribute(str(key), value)
        except Exception:
            pass


def get_tracer() -> Any | None:
    """Expose tracer for advanced callers."""
    return _TRACER
