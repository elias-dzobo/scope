"""Runtime settings used by the API layer.

This module intentionally keeps configuration logic separated from application wiring
so it can be reused by middleware and dependencies in a predictable way.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse


class ProductionConfigError(RuntimeError):
    """Raised when production starts with unsafe or incomplete settings."""


def _to_int(value: str, default: int) -> int:
    """Convert an environment string value to int with a hard fallback to default."""
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _to_bool(value: str, default: bool = False) -> bool:
    """Parse common environment boolean values."""
    normalized = (value or "").strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def _csv(value: str) -> list[str]:
    """Parse a comma-separated environment value into non-empty strings."""
    return [item.strip() for item in value.split(",") if item.strip()]


class ApiConfig:
    """Container for API-level runtime configuration.

    The design uses environment variables to keep deployment behavior outside code.
    """

    def __init__(self) -> None:
        self.environment = os.getenv("SCOPE_ENV", "local").strip().lower() or "local"
        self.is_production = self.environment == "production"
        self.api_key = os.getenv("RESEARCH_API_KEY", "").strip()
        self.rate_limit_per_minute = _to_int(os.getenv("RESEARCH_RATE_LIMIT_PER_MIN", "30"), 30)
        self.rate_limit_burst = max(1, _to_int(os.getenv("RESEARCH_RATE_LIMIT_BURST", "30"), 30))
        self.api_v1_prefix = "/api/v1"
        self.max_limit = _to_int(os.getenv("RESEARCH_API_MAX_LIMIT", "100"), 100)
        self.request_id_header = "x-request-id"
        self.skip_rate_limit_paths = {"/health", "/api/v1/health", "/healthz"}
        self.max_body_bytes = _to_int(os.getenv("SCOPE_MAX_BODY_BYTES", str(25 * 1024 * 1024)), 25 * 1024 * 1024)
        self.allowed_origins = _csv(os.getenv("SCOPE_ALLOWED_ORIGINS", ""))
        if not self.allowed_origins and not self.is_production:
            self.allowed_origins = ["*"]
        self.require_auth = _to_bool(os.getenv("SCOPE_REQUIRE_AUTH", ""), default=self.is_production)
        self.db_backend = os.getenv("SCOPE_DB_BACKEND", "sqlite").strip().lower() or "sqlite"
        self.database_url = os.getenv("DATABASE_URL", "").strip()
        self.artifact_store_backend = os.getenv("ARTIFACT_STORE_BACKEND", "local").strip().lower() or "local"
        self.artifact_bucket = os.getenv("ARTIFACT_BUCKET", "").strip()
        self.artifact_endpoint_url = os.getenv("ARTIFACT_S3_ENDPOINT_URL", "").strip()
        self.auth_allow_dev_google_token = _to_bool(os.getenv("AUTH_ALLOW_DEV_GOOGLE_TOKEN", ""))
        self.google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        self.jwt_secret = os.getenv("JWT_SECRET", "").strip()
        self.observability_enabled_raw = os.getenv("OBSERVABILITY_ENABLED", "").strip()
        self.search_provider = os.getenv("SEARCH_PROVIDER", "auto").strip().lower() or "auto"
        self.validate()

    def validate(self) -> None:
        """Fail fast when production config would expose unsafe behavior."""
        if not self.is_production:
            return

        errors: list[str] = []
        if not self.allowed_origins:
            errors.append("SCOPE_ALLOWED_ORIGINS is required when SCOPE_ENV=production")
        if "*" in self.allowed_origins:
            errors.append("SCOPE_ALLOWED_ORIGINS cannot contain '*' when SCOPE_ENV=production")
        for origin in self.allowed_origins:
            parsed = urlparse(origin)
            if parsed.scheme != "https" or not parsed.netloc:
                errors.append("SCOPE_ALLOWED_ORIGINS must contain only HTTPS origins when SCOPE_ENV=production")
        if not self.require_auth:
            errors.append("SCOPE_REQUIRE_AUTH=true is required when SCOPE_ENV=production")
        if self.auth_allow_dev_google_token:
            errors.append("AUTH_ALLOW_DEV_GOOGLE_TOKEN=false is required when SCOPE_ENV=production")
        if not self.google_client_id:
            errors.append("GOOGLE_CLIENT_ID is required when SCOPE_ENV=production")
        if len(self.jwt_secret) < 32:
            errors.append("JWT_SECRET must be at least 32 characters when SCOPE_ENV=production")
        if not self.observability_enabled_raw:
            errors.append("OBSERVABILITY_ENABLED must be explicitly set when SCOPE_ENV=production")
        if self.db_backend != "postgres":
            errors.append("SCOPE_DB_BACKEND=postgres is required when SCOPE_ENV=production")
        if not self.database_url:
            errors.append("DATABASE_URL is required when SCOPE_ENV=production")
        if self.artifact_store_backend not in {"minio", "s3"}:
            errors.append("ARTIFACT_STORE_BACKEND=minio or s3 is required when SCOPE_ENV=production")
        if not self.artifact_bucket:
            errors.append("ARTIFACT_BUCKET is required when SCOPE_ENV=production")
        if self.artifact_store_backend == "minio" and not self.artifact_endpoint_url:
            errors.append("ARTIFACT_S3_ENDPOINT_URL is required for MinIO when SCOPE_ENV=production")
        if self.search_provider in {"auto", "exa"} and not os.getenv("EXA_API_KEY", "").strip():
            errors.append("EXA_API_KEY is required for SEARCH_PROVIDER=auto|exa when SCOPE_ENV=production")
        if self.search_provider == "tavily" and not (
            os.getenv("TAVILY_API_KEY", "").strip() or os.getenv("TAVI_API_KEY", "").strip()
        ):
            errors.append("TAVILY_API_KEY is required for SEARCH_PROVIDER=tavily when SCOPE_ENV=production")
        if self.search_provider == "serpapi" and not os.getenv("SERPAPI_API_KEY", "").strip():
            errors.append("SERPAPI_API_KEY is required for SEARCH_PROVIDER=serpapi when SCOPE_ENV=production")
        if not (os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()):
            errors.append("At least one LLM key is required when SCOPE_ENV=production")

        if errors:
            raise ProductionConfigError("; ".join(errors))


def load_api_config() -> ApiConfig:
    """Build and return the API config object."""
    return ApiConfig()
