"""Validate that Alembic migrations create the expected Scope schema.

This script upgrades the configured database to the latest revision, then
checks that the product-critical tables and indexes exist. In local/CI usage it
can run against a temporary SQLite path. In production rehearsal it can run
against a disposable Postgres database by setting SCOPE_DB_BACKEND=postgres and
DATABASE_URL.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from research_core.storage import db_path


EXPECTED_TABLES = {
    "advisor_conversations",
    "advisor_messages",
    "advisor_runs",
    "artifact_manifest",
    "billing_plans",
    "credit_reservations",
    "memory_chunks",
    "memory_edges",
    "memory_nodes",
    "research_runs",
    "run_events",
    "usage_ledger",
    "user_onboarding_profiles",
    "user_entitlements",
    "user_sessions",
    "users",
}

EXPECTED_INDEXES = {
    "advisor_conversations": {"idx_advisor_conversations_user_updated"},
    "advisor_messages": {"idx_advisor_messages_conversation_created"},
    "advisor_runs": {"idx_advisor_runs_user_created", "idx_advisor_runs_conversation_created"},
    "artifact_manifest": {"idx_artifact_manifest_run", "idx_artifact_manifest_user_created"},
    "credit_reservations": {"idx_credit_reservations_user_status"},
    "memory_chunks": {"idx_memory_chunks_user_source"},
    "memory_edges": {"idx_memory_edges_source", "idx_memory_edges_target", "idx_memory_edges_user_type"},
    "memory_nodes": {"idx_memory_nodes_user_type", "idx_memory_nodes_user_type_external"},
    "research_runs": {"idx_research_runs_user_created", "idx_research_runs_lease"},
    "run_events": {"idx_run_events_run_created"},
    "usage_ledger": {"idx_usage_ledger_user_created"},
    "user_sessions": {"idx_user_sessions_user_expires"},
}


def _database_url() -> str:
    """Resolve the database URL without importing Alembic's runtime env."""
    backend = os.getenv("SCOPE_DB_BACKEND", "sqlite").strip().lower() or "sqlite"
    if backend == "postgres":
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            raise RuntimeError("DATABASE_URL is required when SCOPE_DB_BACKEND=postgres")
        return url
    path = Path(os.getenv("SCOPE_DB_PATH", str(db_path())))
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def validate_schema() -> dict[str, object]:
    """Run migrations and return a schema validation report."""
    tempdir: tempfile.TemporaryDirectory[str] | None = None
    if (
        os.getenv("SCOPE_DB_BACKEND", "sqlite").strip().lower() != "postgres"
        and os.getenv("SCOPE_VALIDATE_MIGRATIONS_IN_PLACE", "false").lower() != "true"
        and "SCOPE_DB_PATH" not in os.environ
    ):
        tempdir = tempfile.TemporaryDirectory()
        os.environ["SCOPE_DB_PATH"] = str(Path(tempdir.name) / "migration-smoke.db")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    command.upgrade(config, "head")

    engine = create_engine(_database_url())
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    missing_tables = sorted(EXPECTED_TABLES - tables)
    missing_indexes: dict[str, list[str]] = {}

    for table, expected in EXPECTED_INDEXES.items():
        if table not in tables:
            missing_indexes[table] = sorted(expected)
            continue
        actual = {item["name"] for item in inspector.get_indexes(table)}
        missing = sorted(expected - actual)
        if missing:
            missing_indexes[table] = missing

    report = {
        "ok": not missing_tables and not missing_indexes,
        "databaseUrl": _database_url(),
        "missingTables": missing_tables,
        "missingIndexes": missing_indexes,
    }
    if not report["ok"]:
        raise RuntimeError(json.dumps(report, indent=2))
    if tempdir is not None:
        tempdir.cleanup()
    return report


def main() -> None:
    """CLI entrypoint."""
    print(json.dumps(validate_schema(), indent=2))


if __name__ == "__main__":
    main()
