"""HTTP middleware used by the production-oriented API surface.

The middleware intentionally stays at the edge of the system (before request routing)
so authentication, throttling, and correlation IDs are applied consistently across
all routes.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from scope_api.config import ApiConfig
from scope_api.observability.metrics import record_api_request


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a request id to request scope and response header.

    Design note:
      - Use a single correlation id for API logs, DB rows, and job metadata.
      - A UUID is generated when the caller does not send one.
    """

    def __init__(self, app, header_name: str = "x-request-id") -> None:
        super().__init__(app)
        self._header_name = header_name

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get(self._header_name, "").strip() or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[self._header_name] = request_id
        return response


class SlidingWindowRateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory sliding-window limiter used as an API guardrail.

    This protects the service from burst traffic while keeping implementation
    dependency-free for local/initial production deployments.

    Tradeoff:
      - In-memory state is process-local and won't enforce strict global quotas
        across replicas.
      - If horizontal scaling is added later, replace this with Redis-backed storage.
    """

    def __init__(self, app, config: ApiConfig) -> None:
        super().__init__(app)
        self._config = config
        self._window_seconds = 60
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path in self._config.skip_rate_limit_paths:
            return await call_next(request)

        key = request.headers.get("x-api-key") or (request.client.host if request.client else "unknown")
        bucket = self._buckets[key]
        now = time.time()
        window_start = now - self._window_seconds

        async with self._lock:
            while bucket and bucket[0] < window_start:
                bucket.popleft()

            if len(bucket) >= self._config.rate_limit_per_minute:
                retry_after = int(max(1.0, self._window_seconds - (now - bucket[0])))
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded",
                        "retry_after_seconds": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            bucket.append(now)

            while len(bucket) > self._config.rate_limit_burst:
                bucket.popleft()

        return await call_next(request)


class RequestBodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized requests before route handlers parse them."""

    def __init__(self, app, config: ApiConfig) -> None:
        super().__init__(app)
        self._config = config

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError:
                size = 0
            if size > self._config.max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        return await call_next(request)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Capture API metrics for each request in a Prometheus-friendly shape."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        start = time.perf_counter()
        response: Response
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            route = request.url.path
            route_obj = request.scope.get("route")
            if route_obj is not None:
                route = getattr(route_obj, "path", route)

            record_api_request(
                method=request.method,
                route=route,
                status_code=status_code,
                duration_seconds=duration,
            )
