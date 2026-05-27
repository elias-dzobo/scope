"""Runtime storage helpers for research artifacts.

The ArtifactStore interface gives the research code one way to write artifacts
while allowing local filesystem storage in development and S3-compatible
storage such as MinIO in production.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlparse
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


def storage_dir() -> Path:
    """Return the root directory for local runtime state."""
    return Path(os.getenv("SCOPE_STORAGE_DIR", "storage"))


def artifacts_dir() -> Path:
    """Return the directory where new research artifacts should be written."""
    return Path(os.getenv("SCOPE_ARTIFACTS_DIR", str(storage_dir() / "artifacts")))


def db_path() -> Path:
    """Return the SQLite database path for research-run state."""
    return Path(os.getenv("SCOPE_DB_PATH", str(storage_dir() / "research_runs.db")))


def ticker_artifact_dir(ticker: str) -> Path:
    """Return the artifact directory for a ticker symbol."""
    return artifacts_dir() / ticker.strip().upper()


class ArtifactStore(ABC):
    """Storage boundary for generated research artifacts."""

    backend_name = "unknown"

    @abstractmethod
    def write_text(self, key: str, text: str, content_type: str = "text/plain") -> str:
        """Write text content and return the stored artifact URI/key."""

    @abstractmethod
    def write_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Write binary content and return the stored artifact URI/key."""

    def delete_uri(self, uri: str) -> bool:
        """Delete a previously stored artifact URI when supported."""
        return False

    def write_json(self, key: str, payload: Any) -> str:
        """Write JSON with stable indentation."""
        return self.write_text(
            key,
            json.dumps(payload, indent=2, ensure_ascii=False),
            content_type="application/json",
        )


class LocalArtifactStore(ArtifactStore):
    """Filesystem-backed ArtifactStore."""

    backend_name = "local"

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else artifacts_dir()

    def write_text(self, key: str, text: str, content_type: str = "text/plain") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)

    def write_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def delete_uri(self, uri: str) -> bool:
        """Delete a local artifact if it lives under this store root."""
        path = Path(uri)
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError:
            return False
        if not path.exists() or not path.is_file():
            return False
        path.unlink()
        _remove_empty_parents(path.parent, self.root)
        return True

    def _path(self, key: str) -> Path:
        clean = key.strip().lstrip("/")
        if ".." in Path(clean).parts:
            raise ValueError(f"Unsafe artifact key: {key}")
        return self.root / clean


class S3ArtifactStore(ArtifactStore):
    """S3-compatible artifact storage.

    This is designed for MinIO, Cloudflare R2, Backblaze B2, Wasabi, or AWS S3.
    It imports boto3 lazily so local development does not require the package
    unless this backend is selected.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        backend_name: str = "s3",
    ) -> None:
        if not bucket:
            raise ValueError("Artifact bucket is required for S3-compatible storage")
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - depends on optional backend config
            raise RuntimeError("boto3 is required for ARTIFACT_STORE_BACKEND=s3") from exc
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.backend_name = backend_name
        self.client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region_name or None)

    def write_text(self, key: str, text: str, content_type: str = "text/plain") -> str:
        return self.write_bytes(key, text.encode("utf-8"), content_type=content_type)

    def write_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        object_key = self._object_key(key)
        self.client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )
        return f"s3://{self.bucket}/{object_key}"

    def delete_uri(self, uri: str) -> bool:
        """Delete an S3-compatible object created by this store."""
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or parsed.netloc != self.bucket:
            return False
        key = parsed.path.lstrip("/")
        if not key:
            return False
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def _object_key(self, key: str) -> str:
        clean = key.strip().lstrip("/")
        if ".." in Path(clean).parts:
            raise ValueError(f"Unsafe artifact key: {key}")
        return f"{self.prefix}/{clean}" if self.prefix else clean


def artifact_store() -> ArtifactStore:
    """Return the configured artifact store."""
    backend = os.getenv("ARTIFACT_STORE_BACKEND", "local").strip().lower() or "local"
    if backend == "local":
        return LocalArtifactStore()
    if backend in {"s3", "minio"}:
        return S3ArtifactStore(
            bucket=os.getenv("ARTIFACT_BUCKET", ""),
            prefix=os.getenv("ARTIFACT_PREFIX", "scope"),
            endpoint_url=os.getenv("ARTIFACT_S3_ENDPOINT_URL", ""),
            region_name=os.getenv("ARTIFACT_S3_REGION", ""),
            backend_name=backend,
        )
    raise RuntimeError(f"Unsupported ARTIFACT_STORE_BACKEND={backend!r}")


def ticker_artifact_key(ticker: str, *parts: str) -> str:
    """Build a portable object key for a ticker artifact."""
    clean_ticker = ticker.strip().upper() or "UNKNOWN"
    return "/".join([clean_ticker, *[part.strip("/") for part in parts if part]])


def _remove_empty_parents(path: Path, stop_at: Path) -> None:
    """Remove empty directories after deleting a local artifact."""
    stop = stop_at.resolve()
    current = path.resolve()
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
