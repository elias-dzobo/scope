"""FastAPI dependencies for API-level concerns.

Keeping these in one module makes auth and request-context usage explicit and
testable without requiring route-level duplication.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from scope_api.config import ApiConfig, load_api_config


def get_config() -> ApiConfig:
    """Load API config lazily for dependency injection."""
    return load_api_config()


def get_request_id(request: Request) -> str:
    """Return the request id attached by the correlation middleware."""
    return getattr(request.state, "request_id", "")


def require_api_key(
    request: Request,
    api_key: str | None = Header(default=None, alias="x-api-key"),
    config: ApiConfig = Depends(get_config),
) -> str | None:
    """Validate API key when configured.

    If no API key is configured through `RESEARCH_API_KEY`, the endpoint is open.
    """
    if not config.api_key:
        return api_key

    if not api_key or api_key != config.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    return api_key

