"""Regression tests for the compatibility-first monorepo boundaries."""

from __future__ import annotations

import importlib

from scripts.validate_migrations import validate_schema
from research_core.storage import artifacts_dir, db_path, ticker_artifact_dir


def test_canonical_packages_import() -> None:
    """Canonical package names resolve correctly via editable installs."""
    assert importlib.import_module("scope_api.app").app.title == "Scope Stock Research API"
    assert importlib.import_module("research_core.harness").ResearchPlanner
    assert importlib.import_module("provider_integrations.search").search_web
    assert importlib.import_module("agent_core.harness").AgentHarness


def test_storage_paths_are_env_configurable(monkeypatch, tmp_path) -> None:
    """Runtime storage helpers should honor the new storage env vars."""
    artifact_root = tmp_path / "custom-artifacts"
    database_path = tmp_path / "custom-runs.db"
    monkeypatch.setenv("SCOPE_ARTIFACTS_DIR", str(artifact_root))
    monkeypatch.setenv("SCOPE_DB_PATH", str(database_path))

    assert artifacts_dir() == artifact_root
    assert ticker_artifact_dir("lly") == artifact_root / "LLY"
    assert db_path() == database_path


def test_alembic_migrations_create_expected_schema(monkeypatch, tmp_path) -> None:
    """Migration validation should catch missing product tables or indexes."""
    monkeypatch.setenv("SCOPE_DB_BACKEND", "sqlite")
    monkeypatch.setenv("SCOPE_DB_PATH", str(tmp_path / "migration-smoke.db"))

    report = validate_schema()

    assert report["ok"] is True
    assert report["missingTables"] == []
    assert report["missingIndexes"] == {}
