"""Persistence layer for research runs.

SQLite is the default local backend. The module also exposes explicit backend
configuration so the API can fail clearly when Postgres is selected before a
driver/adapter is installed.
"""

from __future__ import annotations

import json
import os
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from research_core.storage import db_path

DB_PATH = db_path()


def database_backend() -> str:
    """Return the configured database backend."""
    return os.getenv("SCOPE_DB_BACKEND", "sqlite").strip().lower() or "sqlite"


def database_url() -> str:
    """Return the configured database URL/path for operational diagnostics."""
    if database_backend() == "postgres":
        return os.getenv("DATABASE_URL", "").strip()
    return str(DB_PATH)


def _ensure_supported_backend() -> None:
    """Fail loudly for unsupported persistence backends."""
    backend = database_backend()
    if backend in {"sqlite", "postgres"}:
        return
    raise RuntimeError(f"Unsupported SCOPE_DB_BACKEND={backend!r}")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    _ensure_supported_backend()
    if database_backend() != "sqlite":
        raise RuntimeError("_connect is SQLite-only; use _connect_postgres for Postgres.")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_postgres():
    """Open a Postgres connection using DATABASE_URL."""
    _ensure_supported_backend()
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required when SCOPE_DB_BACKEND=postgres")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("psycopg is required for SCOPE_DB_BACKEND=postgres") from exc
    return psycopg.connect(url, row_factory=dict_row)


def init_db() -> None:
    if database_backend() == "postgres":
        return _init_db_postgres()

    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_runs (
                id TEXT PRIMARY KEY,
                user_id TEXT DEFAULT '',
                company_name TEXT NOT NULL,
                ticker TEXT NOT NULL,
                selected_pillars_json TEXT NOT NULL,
                status TEXT NOT NULL,
                current_stage TEXT NOT NULL,
                current_substep TEXT NOT NULL DEFAULT '',
                progress REAL NOT NULL DEFAULT 0,
                stage_progress REAL NOT NULL DEFAULT 0,
                activity_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT DEFAULT '',
                summary_json TEXT DEFAULT '',
                result_json TEXT DEFAULT '',
                profile_snapshot_json TEXT NOT NULL DEFAULT '{}',
                profile_snapshot_captured_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                queued_at TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT '',
                failed_at TEXT NOT NULL DEFAULT '',
                lease_owner TEXT NOT NULL DEFAULT '',
                lease_expires_at TEXT NOT NULL DEFAULT '',
                heartbeat_at TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 1,
                budget_snapshot_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                last_activity_at TEXT NOT NULL DEFAULT '',
                completed_at TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                avatar_url TEXT NOT NULL DEFAULT '',
                google_sub TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_secret_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                user_agent TEXT NOT NULL DEFAULT '',
                ip_address TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES research_runs(id)
            );

            CREATE TABLE IF NOT EXISTS user_onboarding_profiles (
                user_id TEXT PRIMARY KEY,
                answers_json TEXT NOT NULL,
                financial_profile_json TEXT NOT NULL,
                risk_profile_json TEXT NOT NULL,
                investor_context_json TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'medium',
                missing_flags_json TEXT NOT NULL DEFAULT '[]',
                profile_narrative_json TEXT NOT NULL DEFAULT '{}',
                profile_synthesis_source TEXT NOT NULL DEFAULT 'deterministic_fallback',
                profile_version TEXT NOT NULL DEFAULT 'v1',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS artifact_manifest (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT '',
                ticker TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                storage_backend TEXT NOT NULL,
                storage_uri TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                checksum TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES research_runs(id)
            );

            CREATE TABLE IF NOT EXISTS memory_nodes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                node_type TEXT NOT NULL,
                external_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                properties_json TEXT NOT NULL DEFAULT '{}',
                source_ref_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_edges (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1,
                properties_json TEXT NOT NULL DEFAULT '{}',
                source_ref_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_node_id) REFERENCES memory_nodes(id),
                FOREIGN KEY (target_node_id) REFERENCES memory_nodes(id)
            );

            CREATE TABLE IF NOT EXISTS memory_chunks (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                text TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (node_id) REFERENCES memory_nodes(id)
            );

            CREATE TABLE IF NOT EXISTS advisor_runs (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL,
                query TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                plan_json TEXT NOT NULL DEFAULT '{}',
                context_json TEXT NOT NULL DEFAULT '{}',
                coverage_json TEXT NOT NULL DEFAULT '{}',
                research_requests_json TEXT NOT NULL DEFAULT '[]',
                answer_json TEXT NOT NULL DEFAULT '{}',
                trace_json TEXT NOT NULL DEFAULT '[]',
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS advisor_conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                active_topic TEXT NOT NULL DEFAULT '',
                active_research_run_id TEXT NOT NULL DEFAULT '',
                active_generic_research_id TEXT NOT NULL DEFAULT '',
                active_entities_json TEXT NOT NULL DEFAULT '[]',
                active_themes_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS advisor_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                advisor_run_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES advisor_conversations(id)
            );

            CREATE TABLE IF NOT EXISTS billing_plans (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                monthly_included_credits REAL NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_entitlements (
                user_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL DEFAULT 'free',
                billing_provider TEXT NOT NULL DEFAULT 'local',
                provider_customer_id TEXT NOT NULL DEFAULT '',
                provider_subscription_id TEXT NOT NULL DEFAULT '',
                subscription_status TEXT NOT NULL DEFAULT 'inactive',
                credit_balance REAL NOT NULL DEFAULT 0,
                monthly_credit_allowance REAL NOT NULL DEFAULT 0,
                current_period_started_at TEXT NOT NULL DEFAULT '',
                current_period_ends_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS usage_ledger (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '',
                advisor_run_id TEXT NOT NULL DEFAULT '',
                credits_delta REAL NOT NULL,
                raw_cost_estimate_json TEXT NOT NULL DEFAULT '{}',
                reason TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS credit_reservations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '',
                advisor_run_id TEXT NOT NULL DEFAULT '',
                credits_reserved REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                finalized_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )
        _ensure_column(conn, "research_runs", "current_substep", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "research_runs", "user_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "research_runs", "stage_progress", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "research_runs", "activity_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "research_runs", "last_activity_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "research_runs", "profile_snapshot_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "research_runs", "profile_snapshot_captured_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "research_runs", "queued_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "research_runs", "started_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "research_runs", "failed_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "research_runs", "lease_owner", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "research_runs", "lease_expires_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "research_runs", "heartbeat_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "research_runs", "retry_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "research_runs", "max_retries", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "research_runs", "budget_snapshot_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "user_onboarding_profiles", "profile_narrative_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "advisor_runs", "conversation_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(
            conn,
            "user_onboarding_profiles",
            "profile_synthesis_source",
            "TEXT NOT NULL DEFAULT 'deterministic_fallback'",
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_manifest_run ON artifact_manifest(run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_research_runs_lease ON research_runs(status, lease_expires_at, queued_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_user_expires ON user_sessions(user_id, expires_at)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifact_manifest_user_created ON artifact_manifest(user_id, created_at)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_nodes_user_type ON memory_nodes(user_id, node_type)")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_nodes_user_type_external
                ON memory_nodes(user_id, node_type, external_id)
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_edges_user_type ON memory_edges(user_id, edge_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_edges_source ON memory_edges(source_node_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_edges_target ON memory_edges(target_node_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_chunks_user_source ON memory_chunks(user_id, source_type, source_id)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_advisor_runs_user_created ON advisor_runs(user_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_advisor_runs_conversation_created ON advisor_runs(conversation_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_advisor_conversations_user_updated ON advisor_conversations(user_id, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_advisor_messages_conversation_created ON advisor_messages(conversation_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_ledger_user_created ON usage_ledger(user_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_credit_reservations_user_status ON credit_reservations(user_id, status)")
        conn.commit()
    finally:
        conn.close()


def _init_db_postgres() -> None:
    """Initialize the Postgres schema used by the API."""
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    avatar_url TEXT NOT NULL DEFAULT '',
                    google_sub TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    session_secret_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    user_agent TEXT NOT NULL DEFAULT '',
                    ip_address TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS research_runs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT '',
                    company_name TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    selected_pillars_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    current_substep TEXT NOT NULL DEFAULT '',
                    progress DOUBLE PRECISION NOT NULL DEFAULT 0,
                    stage_progress DOUBLE PRECISION NOT NULL DEFAULT 0,
                    activity_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT DEFAULT '',
                    summary_json TEXT DEFAULT '',
                    result_json TEXT DEFAULT '',
                    profile_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    profile_snapshot_captured_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    queued_at TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT '',
                    failed_at TEXT NOT NULL DEFAULT '',
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_expires_at TEXT NOT NULL DEFAULT '',
                    heartbeat_at TEXT NOT NULL DEFAULT '',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 1,
                    budget_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    last_activity_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    id BIGSERIAL PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id),
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_onboarding_profiles (
                    user_id TEXT PRIMARY KEY REFERENCES users(id),
                    answers_json TEXT NOT NULL,
                    financial_profile_json TEXT NOT NULL,
                    risk_profile_json TEXT NOT NULL,
                    investor_context_json TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'medium',
                    missing_flags_json TEXT NOT NULL DEFAULT '[]',
                    profile_narrative_json TEXT NOT NULL DEFAULT '{}',
                    profile_synthesis_source TEXT NOT NULL DEFAULT 'deterministic_fallback',
                    profile_version TEXT NOT NULL DEFAULT 'v1',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifact_manifest (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id),
                    user_id TEXT NOT NULL DEFAULT '',
                    ticker TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    storage_backend TEXT NOT NULL,
                    storage_uri TEXT NOT NULL,
                    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    checksum TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_nodes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    external_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    source_ref_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_edges (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    source_node_id TEXT NOT NULL REFERENCES memory_nodes(id),
                    target_node_id TEXT NOT NULL REFERENCES memory_nodes(id),
                    edge_type TEXT NOT NULL,
                    confidence DOUBLE PRECISION NOT NULL DEFAULT 1,
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    source_ref_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_chunks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    node_id TEXT NOT NULL REFERENCES memory_nodes(id),
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS advisor_runs (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    coverage_json TEXT NOT NULL DEFAULT '{}',
                    research_requests_json TEXT NOT NULL DEFAULT '[]',
                    answer_json TEXT NOT NULL DEFAULT '{}',
                    trace_json TEXT NOT NULL DEFAULT '[]',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS advisor_conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    active_topic TEXT NOT NULL DEFAULT '',
                    active_research_run_id TEXT NOT NULL DEFAULT '',
                    active_generic_research_id TEXT NOT NULL DEFAULT '',
                    active_entities_json TEXT NOT NULL DEFAULT '[]',
                    active_themes_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS advisor_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES advisor_conversations(id),
                    advisor_run_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS billing_plans (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    monthly_included_credits DOUBLE PRECISION NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_entitlements (
                    user_id TEXT PRIMARY KEY REFERENCES users(id),
                    plan_id TEXT NOT NULL DEFAULT 'free',
                    billing_provider TEXT NOT NULL DEFAULT 'local',
                    provider_customer_id TEXT NOT NULL DEFAULT '',
                    provider_subscription_id TEXT NOT NULL DEFAULT '',
                    subscription_status TEXT NOT NULL DEFAULT 'inactive',
                    credit_balance DOUBLE PRECISION NOT NULL DEFAULT 0,
                    monthly_credit_allowance DOUBLE PRECISION NOT NULL DEFAULT 0,
                    current_period_started_at TEXT NOT NULL DEFAULT '',
                    current_period_ends_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS usage_ledger (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    event_type TEXT NOT NULL,
                    run_id TEXT NOT NULL DEFAULT '',
                    advisor_run_id TEXT NOT NULL DEFAULT '',
                    credits_delta DOUBLE PRECISION NOT NULL,
                    raw_cost_estimate_json TEXT NOT NULL DEFAULT '{}',
                    reason TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS credit_reservations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    run_id TEXT NOT NULL DEFAULT '',
                    advisor_run_id TEXT NOT NULL DEFAULT '',
                    credits_reserved DOUBLE PRECISION NOT NULL,
                    status TEXT NOT NULL DEFAULT 'reserved',
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    finalized_at TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_research_runs_user_created
                    ON research_runs(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_research_runs_lease
                    ON research_runs(status, lease_expires_at, queued_at);
                CREATE INDEX IF NOT EXISTS idx_user_sessions_user_expires
                    ON user_sessions(user_id, expires_at);
                CREATE INDEX IF NOT EXISTS idx_run_events_run_created
                    ON run_events(run_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_artifact_manifest_run
                    ON artifact_manifest(run_id);
                CREATE INDEX IF NOT EXISTS idx_artifact_manifest_user_created
                    ON artifact_manifest(user_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_memory_nodes_user_type
                    ON memory_nodes(user_id, node_type);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_nodes_user_type_external
                    ON memory_nodes(user_id, node_type, external_id);
                CREATE INDEX IF NOT EXISTS idx_memory_edges_user_type
                    ON memory_edges(user_id, edge_type);
                CREATE INDEX IF NOT EXISTS idx_memory_edges_source
                    ON memory_edges(source_node_id);
                CREATE INDEX IF NOT EXISTS idx_memory_edges_target
                    ON memory_edges(target_node_id);
                CREATE INDEX IF NOT EXISTS idx_memory_chunks_user_source
                    ON memory_chunks(user_id, source_type, source_id);
                CREATE INDEX IF NOT EXISTS idx_advisor_runs_user_created
                    ON advisor_runs(user_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_advisor_runs_conversation_created
                    ON advisor_runs(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_advisor_conversations_user_updated
                    ON advisor_conversations(user_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_advisor_messages_conversation_created
                    ON advisor_messages(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_usage_ledger_user_created
                    ON usage_ledger(user_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_credit_reservations_user_status
                    ON credit_reservations(user_id, status);
                """
            )
            for table, column, ddl in [
                ("research_runs", "current_substep", "TEXT NOT NULL DEFAULT ''"),
                ("research_runs", "user_id", "TEXT NOT NULL DEFAULT ''"),
                ("research_runs", "stage_progress", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
                ("research_runs", "activity_count", "INTEGER NOT NULL DEFAULT 0"),
                ("research_runs", "last_activity_at", "TEXT NOT NULL DEFAULT ''"),
                ("research_runs", "profile_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("research_runs", "profile_snapshot_captured_at", "TEXT NOT NULL DEFAULT ''"),
                ("research_runs", "queued_at", "TEXT NOT NULL DEFAULT ''"),
                ("research_runs", "started_at", "TEXT NOT NULL DEFAULT ''"),
                ("research_runs", "failed_at", "TEXT NOT NULL DEFAULT ''"),
                ("research_runs", "lease_owner", "TEXT NOT NULL DEFAULT ''"),
                ("research_runs", "lease_expires_at", "TEXT NOT NULL DEFAULT ''"),
                ("research_runs", "heartbeat_at", "TEXT NOT NULL DEFAULT ''"),
                ("research_runs", "retry_count", "INTEGER NOT NULL DEFAULT 0"),
                ("research_runs", "max_retries", "INTEGER NOT NULL DEFAULT 1"),
                ("research_runs", "budget_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("user_onboarding_profiles", "profile_narrative_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("advisor_runs", "conversation_id", "TEXT NOT NULL DEFAULT ''"),
                (
                    "user_onboarding_profiles",
                    "profile_synthesis_source",
                    "TEXT NOT NULL DEFAULT 'deterministic_fallback'",
                ),
            ]:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_suffix: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column in existing:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_suffix}")


def create_artifact_record(
    *,
    run_id: str,
    user_id: str | None,
    ticker: str,
    artifact_type: str,
    storage_backend: str,
    storage_uri: str,
    content_type: str = "application/octet-stream",
    size_bytes: int = 0,
    checksum: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Register one stored artifact in the run-owned manifest."""
    if database_backend() == "postgres":
        return _create_artifact_record_postgres(
            run_id=run_id,
            user_id=user_id,
            ticker=ticker,
            artifact_type=artifact_type,
            storage_backend=storage_backend,
            storage_uri=storage_uri,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum=checksum,
            metadata=metadata,
        )
    from uuid import uuid4

    now = utc_now_iso()
    artifact_id = str(uuid4())
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO artifact_manifest (
                id, run_id, user_id, ticker, artifact_type, storage_backend, storage_uri,
                content_type, size_bytes, checksum, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                run_id,
                user_id or "",
                ticker.upper(),
                artifact_type,
                storage_backend,
                storage_uri,
                content_type,
                int(size_bytes or 0),
                checksum,
                json.dumps(metadata or {}),
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM artifact_manifest WHERE id = ?", (artifact_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_artifact(row)


def _create_artifact_record_postgres(
    *,
    run_id: str,
    user_id: str | None,
    ticker: str,
    artifact_type: str,
    storage_backend: str,
    storage_uri: str,
    content_type: str = "application/octet-stream",
    size_bytes: int = 0,
    checksum: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from uuid import uuid4

    now = utc_now_iso()
    artifact_id = str(uuid4())
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO artifact_manifest (
                    id, run_id, user_id, ticker, artifact_type, storage_backend, storage_uri,
                    content_type, size_bytes, checksum, metadata_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    artifact_id,
                    run_id,
                    user_id or "",
                    ticker.upper(),
                    artifact_type,
                    storage_backend,
                    storage_uri,
                    content_type,
                    int(size_bytes or 0),
                    checksum,
                    json.dumps(metadata or {}),
                    now,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return _row_to_artifact(row)


def list_run_artifacts(run_id: str, user_id: str | None = None, anonymous_only: bool = False) -> list[dict[str, Any]]:
    """List artifacts for a run, scoped like research-run access."""
    if database_backend() == "postgres":
        return _list_run_artifacts_postgres(run_id, user_id=user_id, anonymous_only=anonymous_only)
    conn = _connect()
    try:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM artifact_manifest WHERE run_id = ? AND user_id = ? ORDER BY created_at ASC",
                (run_id, user_id),
            ).fetchall()
        elif anonymous_only:
            rows = conn.execute(
                "SELECT * FROM artifact_manifest WHERE run_id = ? AND user_id = '' ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM artifact_manifest WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_artifact(row) for row in rows]


def _list_run_artifacts_postgres(run_id: str, user_id: str | None = None, anonymous_only: bool = False) -> list[dict[str, Any]]:
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT * FROM artifact_manifest WHERE run_id = %s AND user_id = %s ORDER BY created_at ASC",
                    (run_id, user_id),
                )
            elif anonymous_only:
                cur.execute(
                    "SELECT * FROM artifact_manifest WHERE run_id = %s AND user_id = '' ORDER BY created_at ASC",
                    (run_id,),
                )
            else:
                cur.execute("SELECT * FROM artifact_manifest WHERE run_id = %s ORDER BY created_at ASC", (run_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_artifact(row) for row in rows]


def list_user_artifacts(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """List recent artifacts owned by a user."""
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM artifact_manifest WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                    (user_id, limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_artifact(row) for row in rows]
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM artifact_manifest WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_artifact(row) for row in rows]


def _row_to_artifact(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "user_id": row["user_id"] or "",
        "ticker": row["ticker"],
        "artifact_type": row["artifact_type"],
        "storage_backend": row["storage_backend"],
        "storage_uri": row["storage_uri"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"] or 0,
        "checksum": row["checksum"] or "",
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "created_at": row["created_at"],
    }


def upsert_memory_node(
    *,
    user_id: str,
    node_type: str,
    external_id: str,
    title: str = "",
    summary: str = "",
    properties: dict[str, Any] | None = None,
    source_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a typed user-memory node.

    Nodes are the durable concepts in the personalization graph: users,
    companies, research runs, recommendations, pillars, risks, and documents.
    The `(user_id, node_type, external_id)` tuple is the natural key.
    """
    if database_backend() == "postgres":
        return _upsert_memory_node_postgres(
            user_id=user_id,
            node_type=node_type,
            external_id=external_id,
            title=title,
            summary=summary,
            properties=properties,
            source_ref=source_ref,
        )
    from uuid import uuid4

    now = utc_now_iso()
    normalized_external_id = external_id or f"generated:{uuid4()}"
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT * FROM memory_nodes
            WHERE user_id = ? AND node_type = ? AND external_id = ?
            """,
            (user_id, node_type, normalized_external_id),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE memory_nodes
                SET title = ?, summary = ?, properties_json = ?, source_ref_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    summary,
                    json.dumps(properties or {}),
                    json.dumps(source_ref or {}),
                    now,
                    row["id"],
                ),
            )
            node_id = row["id"]
        else:
            node_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO memory_nodes (
                    id, user_id, node_type, external_id, title, summary,
                    properties_json, source_ref_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    user_id,
                    node_type,
                    normalized_external_id,
                    title,
                    summary,
                    json.dumps(properties or {}),
                    json.dumps(source_ref or {}),
                    now,
                    now,
                ),
            )
        conn.commit()
        saved = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_memory_node(saved)


def _upsert_memory_node_postgres(
    *,
    user_id: str,
    node_type: str,
    external_id: str,
    title: str = "",
    summary: str = "",
    properties: dict[str, Any] | None = None,
    source_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from uuid import uuid4

    now = utc_now_iso()
    normalized_external_id = external_id or f"generated:{uuid4()}"
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_nodes (
                    id, user_id, node_type, external_id, title, summary,
                    properties_json, source_ref_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, node_type, external_id)
                DO UPDATE SET
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    properties_json = EXCLUDED.properties_json,
                    source_ref_json = EXCLUDED.source_ref_json,
                    updated_at = EXCLUDED.updated_at
                RETURNING *
                """,
                (
                    str(uuid4()),
                    user_id,
                    node_type,
                    normalized_external_id,
                    title,
                    summary,
                    json.dumps(properties or {}),
                    json.dumps(source_ref or {}),
                    now,
                    now,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return _row_to_memory_node(row)


def upsert_memory_edge(
    *,
    user_id: str,
    source_node_id: str,
    target_node_id: str,
    edge_type: str,
    confidence: float = 1.0,
    properties: dict[str, Any] | None = None,
    source_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an edge between two memory nodes or refresh its metadata."""
    if database_backend() == "postgres":
        return _upsert_memory_edge_postgres(
            user_id=user_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            edge_type=edge_type,
            confidence=confidence,
            properties=properties,
            source_ref=source_ref,
        )
    from uuid import uuid4

    now = utc_now_iso()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT * FROM memory_edges
            WHERE user_id = ? AND source_node_id = ? AND target_node_id = ? AND edge_type = ?
            """,
            (user_id, source_node_id, target_node_id, edge_type),
        ).fetchone()
        if row:
            edge_id = row["id"]
            conn.execute(
                """
                UPDATE memory_edges
                SET confidence = ?, properties_json = ?, source_ref_json = ?
                WHERE id = ?
                """,
                (float(confidence), json.dumps(properties or {}), json.dumps(source_ref or {}), edge_id),
            )
        else:
            edge_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO memory_edges (
                    id, user_id, source_node_id, target_node_id, edge_type, confidence,
                    properties_json, source_ref_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge_id,
                    user_id,
                    source_node_id,
                    target_node_id,
                    edge_type,
                    float(confidence),
                    json.dumps(properties or {}),
                    json.dumps(source_ref or {}),
                    now,
                ),
            )
        conn.commit()
        saved = conn.execute("SELECT * FROM memory_edges WHERE id = ?", (edge_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_memory_edge(saved)


def _upsert_memory_edge_postgres(
    *,
    user_id: str,
    source_node_id: str,
    target_node_id: str,
    edge_type: str,
    confidence: float = 1.0,
    properties: dict[str, Any] | None = None,
    source_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from uuid import uuid4

    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM memory_edges
                WHERE user_id = %s AND source_node_id = %s AND target_node_id = %s AND edge_type = %s
                """,
                (user_id, source_node_id, target_node_id, edge_type),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    """
                    UPDATE memory_edges
                    SET confidence = %s, properties_json = %s, source_ref_json = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (float(confidence), json.dumps(properties or {}), json.dumps(source_ref or {}), row["id"]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO memory_edges (
                        id, user_id, source_node_id, target_node_id, edge_type, confidence,
                        properties_json, source_ref_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        str(uuid4()),
                        user_id,
                        source_node_id,
                        target_node_id,
                        edge_type,
                        float(confidence),
                        json.dumps(properties or {}),
                        json.dumps(source_ref or {}),
                        utc_now_iso(),
                    ),
                )
            saved = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return _row_to_memory_edge(saved)


def create_memory_chunk(
    *,
    user_id: str,
    node_id: str,
    source_type: str,
    source_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store a retrievable text chunk attached to a graph node."""
    if database_backend() == "postgres":
        return _create_memory_chunk_postgres(
            user_id=user_id,
            node_id=node_id,
            source_type=source_type,
            source_id=source_id,
            text=text,
            metadata=metadata,
        )
    from uuid import uuid4

    chunk_id = str(uuid4())
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO memory_chunks (id, user_id, node_id, source_type, source_id, text, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (chunk_id, user_id, node_id, source_type, source_id, text, json.dumps(metadata or {}), utc_now_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM memory_chunks WHERE id = ?", (chunk_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_memory_chunk(row)


def _create_memory_chunk_postgres(
    *,
    user_id: str,
    node_id: str,
    source_type: str,
    source_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from uuid import uuid4

    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_chunks (id, user_id, node_id, source_type, source_id, text, metadata_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (str(uuid4()), user_id, node_id, source_type, source_id, text, json.dumps(metadata or {}), utc_now_iso()),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return _row_to_memory_chunk(row)


def search_memory_chunks(user_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Lexically search a user's memory chunks.

    This is the bootstrapping retriever. The API shape intentionally leaves room
    for a later pgvector/GraphRAG implementation without changing callers.
    """
    normalized_limit = max(1, min(int(limit or 10), 50))
    needle = f"%{query.strip()}%"
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.*, n.node_type, n.title AS node_title
                    FROM memory_chunks c
                    JOIN memory_nodes n ON n.id = c.node_id
                    WHERE c.user_id = %s AND (%s = '%%' OR c.text ILIKE %s OR n.title ILIKE %s)
                    ORDER BY c.created_at DESC
                    LIMIT %s
                    """,
                    (user_id, needle, needle, needle, normalized_limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_memory_chunk(row) for row in rows]
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT c.*, n.node_type, n.title AS node_title
            FROM memory_chunks c
            JOIN memory_nodes n ON n.id = c.node_id
            WHERE c.user_id = ? AND (? = '%' OR lower(c.text) LIKE lower(?) OR lower(n.title) LIKE lower(?))
            ORDER BY c.created_at DESC
            LIMIT ?
            """,
            (user_id, needle, needle, needle, normalized_limit),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_memory_chunk(row) for row in rows]


def list_memory_chunks_for_node(user_id: str, node_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """List chunks belonging to one memory node."""
    normalized_limit = max(1, min(int(limit or 20), 100))
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.*, n.node_type, n.title AS node_title
                    FROM memory_chunks c
                    JOIN memory_nodes n ON n.id = c.node_id
                    WHERE c.user_id = %s AND c.node_id = %s
                    ORDER BY c.created_at DESC
                    LIMIT %s
                    """,
                    (user_id, node_id, normalized_limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_memory_chunk(row) for row in rows]
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT c.*, n.node_type, n.title AS node_title
            FROM memory_chunks c
            JOIN memory_nodes n ON n.id = c.node_id
            WHERE c.user_id = ? AND c.node_id = ?
            ORDER BY c.created_at DESC
            LIMIT ?
            """,
            (user_id, node_id, normalized_limit),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_memory_chunk(row) for row in rows]


def list_memory_nodes(user_id: str, node_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List recent nodes for a user, optionally filtered by type."""
    normalized_limit = max(1, min(int(limit or 50), 100))
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                if node_type:
                    cur.execute(
                        """
                        SELECT * FROM memory_nodes
                        WHERE user_id = %s AND node_type = %s
                        ORDER BY updated_at DESC
                        LIMIT %s
                        """,
                        (user_id, node_type, normalized_limit),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM memory_nodes WHERE user_id = %s ORDER BY updated_at DESC LIMIT %s",
                        (user_id, normalized_limit),
                    )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_memory_node(row) for row in rows]
    conn = _connect()
    try:
        if node_type:
            rows = conn.execute(
                """
                SELECT * FROM memory_nodes
                WHERE user_id = ? AND node_type = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, node_type, normalized_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memory_nodes WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, normalized_limit),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_memory_node(row) for row in rows]


def get_memory_neighbors(user_id: str, node_id: str, limit: int = 25) -> list[dict[str, Any]]:
    """Return adjacent graph nodes for one memory node."""
    normalized_limit = max(1, min(int(limit or 25), 100))
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT e.*, n.id AS neighbor_id, n.node_type AS neighbor_type,
                        n.title AS neighbor_title, n.summary AS neighbor_summary,
                        n.properties_json AS neighbor_properties_json
                    FROM memory_edges e
                    JOIN memory_nodes n
                        ON n.id = CASE WHEN e.source_node_id = %s THEN e.target_node_id ELSE e.source_node_id END
                    WHERE e.user_id = %s AND (e.source_node_id = %s OR e.target_node_id = %s)
                    LIMIT %s
                    """,
                    (node_id, user_id, node_id, node_id, normalized_limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_memory_neighbor(row) for row in rows]
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT e.*, n.id AS neighbor_id, n.node_type AS neighbor_type,
                n.title AS neighbor_title, n.summary AS neighbor_summary,
                n.properties_json AS neighbor_properties_json
            FROM memory_edges e
            JOIN memory_nodes n
                ON n.id = CASE WHEN e.source_node_id = ? THEN e.target_node_id ELSE e.source_node_id END
            WHERE e.user_id = ? AND (e.source_node_id = ? OR e.target_node_id = ?)
            LIMIT ?
            """,
            (node_id, user_id, node_id, node_id, normalized_limit),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_memory_neighbor(row) for row in rows]


def _row_to_memory_node(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "node_type": row["node_type"],
        "external_id": row["external_id"] or "",
        "title": row["title"] or "",
        "summary": row["summary"] or "",
        "properties": json.loads(row["properties_json"] or "{}"),
        "source_ref": json.loads(row["source_ref_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_memory_edge(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "source_node_id": row["source_node_id"],
        "target_node_id": row["target_node_id"],
        "edge_type": row["edge_type"],
        "confidence": float(row["confidence"] or 0),
        "properties": json.loads(row["properties_json"] or "{}"),
        "source_ref": json.loads(row["source_ref_json"] or "{}"),
        "created_at": row["created_at"],
    }


def _row_to_memory_chunk(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "user_id": row["user_id"],
        "node_id": row["node_id"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "text": row["text"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "created_at": row["created_at"],
    }
    if "node_type" in row.keys() if hasattr(row, "keys") else "node_type" in row:
        out["node_type"] = row["node_type"]
        out["node_title"] = row["node_title"] or ""
    return out


def _row_to_memory_neighbor(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "edge": _row_to_memory_edge(row),
        "node": {
            "id": row["neighbor_id"],
            "node_type": row["neighbor_type"],
            "title": row["neighbor_title"] or "",
            "summary": row["neighbor_summary"] or "",
            "properties": json.loads(row["neighbor_properties_json"] or "{}"),
        },
    }


def create_advisor_run(
    *,
    user_id: str,
    query: str,
    mode: str = "auto",
    conversation_id: str = "",
) -> dict[str, Any]:
    """Create a durable advisor harness run for traceable evaluation."""
    if database_backend() == "postgres":
        return _create_advisor_run_postgres(
            user_id=user_id,
            query=query,
            mode=mode,
            conversation_id=conversation_id,
        )
    from uuid import uuid4

    run_id = str(uuid4())
    now = utc_now_iso()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO advisor_runs (
                id, conversation_id, user_id, query, mode, status, plan_json, context_json, coverage_json,
                research_requests_json, answer_json, trace_json, error_message,
                created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, 'running', '{}', '{}', '{}', '[]', '{}', '[]', '', ?, ?, '')
            """,
            (run_id, conversation_id, user_id, query, mode, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM advisor_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_advisor_run(row)


def _create_advisor_run_postgres(
    *,
    user_id: str,
    query: str,
    mode: str = "auto",
    conversation_id: str = "",
) -> dict[str, Any]:
    from uuid import uuid4

    run_id = str(uuid4())
    now = utc_now_iso()
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO advisor_runs (
                    id, conversation_id, user_id, query, mode, status, plan_json, context_json, coverage_json,
                    research_requests_json, answer_json, trace_json, error_message,
                    created_at, updated_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, 'running', '{}', '{}', '{}', '[]', '{}', '[]', '', %s, %s, '')
                RETURNING *
                """,
                (run_id, conversation_id, user_id, query, mode, now, now),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return _row_to_advisor_run(row)


def update_advisor_run(
    run_id: str,
    *,
    status: str | None = None,
    plan: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    research_requests: list[dict[str, Any]] | None = None,
    answer: dict[str, Any] | None = None,
    trace: list[dict[str, Any]] | None = None,
    error_message: str | None = None,
    completed: bool = False,
) -> None:
    """Update an advisor run with the latest harness artifacts."""
    if database_backend() == "postgres":
        return _update_advisor_run_postgres(
            run_id,
            status=status,
            plan=plan,
            context=context,
            coverage=coverage,
            research_requests=research_requests,
            answer=answer,
            trace=trace,
            error_message=error_message,
            completed=completed,
        )
    fields: list[str] = ["updated_at = ?"]
    values: list[Any] = [utc_now_iso()]
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if plan is not None:
        fields.append("plan_json = ?")
        values.append(json.dumps(plan))
    if context is not None:
        fields.append("context_json = ?")
        values.append(json.dumps(context))
    if coverage is not None:
        fields.append("coverage_json = ?")
        values.append(json.dumps(coverage))
    if research_requests is not None:
        fields.append("research_requests_json = ?")
        values.append(json.dumps(research_requests))
    if answer is not None:
        fields.append("answer_json = ?")
        values.append(json.dumps(answer))
    if trace is not None:
        fields.append("trace_json = ?")
        values.append(json.dumps(trace))
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    if completed:
        fields.append("completed_at = ?")
        values.append(utc_now_iso())
    values.append(run_id)
    conn = _connect()
    try:
        conn.execute(f"UPDATE advisor_runs SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def _update_advisor_run_postgres(
    run_id: str,
    *,
    status: str | None = None,
    plan: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    research_requests: list[dict[str, Any]] | None = None,
    answer: dict[str, Any] | None = None,
    trace: list[dict[str, Any]] | None = None,
    error_message: str | None = None,
    completed: bool = False,
) -> None:
    fields: list[str] = ["updated_at = %s"]
    values: list[Any] = [utc_now_iso()]
    if status is not None:
        fields.append("status = %s")
        values.append(status)
    if plan is not None:
        fields.append("plan_json = %s")
        values.append(json.dumps(plan))
    if context is not None:
        fields.append("context_json = %s")
        values.append(json.dumps(context))
    if coverage is not None:
        fields.append("coverage_json = %s")
        values.append(json.dumps(coverage))
    if research_requests is not None:
        fields.append("research_requests_json = %s")
        values.append(json.dumps(research_requests))
    if answer is not None:
        fields.append("answer_json = %s")
        values.append(json.dumps(answer))
    if trace is not None:
        fields.append("trace_json = %s")
        values.append(json.dumps(trace))
    if error_message is not None:
        fields.append("error_message = %s")
        values.append(error_message)
    if completed:
        fields.append("completed_at = %s")
        values.append(utc_now_iso())
    values.append(run_id)
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE advisor_runs SET {', '.join(fields)} WHERE id = %s", values)
        conn.commit()
    finally:
        conn.close()


def get_advisor_run(run_id: str, user_id: str) -> dict[str, Any] | None:
    """Load one advisor harness run owned by a user."""
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM advisor_runs WHERE id = %s AND user_id = %s", (run_id, user_id))
                row = cur.fetchone()
        finally:
            conn.close()
        return _row_to_advisor_run(row) if row else None
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM advisor_runs WHERE id = ? AND user_id = ?", (run_id, user_id)).fetchone()
    finally:
        conn.close()
    return _row_to_advisor_run(row) if row else None


def list_advisor_runs(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """List recent advisor harness runs for a user."""
    normalized_limit = max(1, min(int(limit or 20), 100))
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM advisor_runs WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                    (user_id, normalized_limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_advisor_run(row) for row in rows]
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM advisor_runs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, normalized_limit),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_advisor_run(row) for row in rows]


def create_advisor_conversation(
    *,
    user_id: str,
    title: str = "",
    active_topic: str = "",
    active_research_run_id: str = "",
    active_generic_research_id: str = "",
    active_entities: list[str] | None = None,
    active_themes: list[str] | None = None,
) -> dict[str, Any]:
    """Create a durable advisor conversation thread."""
    from uuid import uuid4

    conversation_id = str(uuid4())
    now = utc_now_iso()
    entities = active_entities or []
    themes = active_themes or []
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO advisor_conversations (
                        id, user_id, title, active_topic, active_research_run_id,
                        active_generic_research_id, active_entities_json, active_themes_json,
                        status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)
                    RETURNING *
                    """,
                    (
                        conversation_id,
                        user_id,
                        title,
                        active_topic,
                        active_research_run_id,
                        active_generic_research_id,
                        json.dumps(entities),
                        json.dumps(themes),
                        now,
                        now,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        return _row_to_advisor_conversation(row)
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO advisor_conversations (
                id, user_id, title, active_topic, active_research_run_id,
                active_generic_research_id, active_entities_json, active_themes_json,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                conversation_id,
                user_id,
                title,
                active_topic,
                active_research_run_id,
                active_generic_research_id,
                json.dumps(entities),
                json.dumps(themes),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM advisor_conversations WHERE id = ?", (conversation_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_advisor_conversation(row)


def get_advisor_conversation(conversation_id: str, user_id: str) -> dict[str, Any] | None:
    """Load one advisor conversation owned by a user."""
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM advisor_conversations WHERE id = %s AND user_id = %s",
                    (conversation_id, user_id),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return _row_to_advisor_conversation(row) if row else None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM advisor_conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_advisor_conversation(row) if row else None


def list_advisor_conversations(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """List recent advisor conversation threads for a user."""
    normalized_limit = max(1, min(int(limit or 20), 100))
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM advisor_conversations WHERE user_id = %s ORDER BY updated_at DESC LIMIT %s",
                    (user_id, normalized_limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_advisor_conversation(row) for row in rows]
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM advisor_conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
            (user_id, normalized_limit),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_advisor_conversation(row) for row in rows]


def update_advisor_conversation(
    conversation_id: str,
    *,
    user_id: str,
    title: str | None = None,
    active_topic: str | None = None,
    active_research_run_id: str | None = None,
    active_generic_research_id: str | None = None,
    active_entities: list[str] | None = None,
    active_themes: list[str] | None = None,
    status: str | None = None,
) -> None:
    """Update thread-level conversation state."""
    assignments = ["updated_at = ?"]
    pg_assignments = ["updated_at = %s"]
    values: list[Any] = [utc_now_iso()]
    for column, value in [
        ("title", title),
        ("active_topic", active_topic),
        ("active_research_run_id", active_research_run_id),
        ("active_generic_research_id", active_generic_research_id),
        ("status", status),
    ]:
        if value is not None:
            assignments.append(f"{column} = ?")
            pg_assignments.append(f"{column} = %s")
            values.append(value)
    if active_entities is not None:
        assignments.append("active_entities_json = ?")
        pg_assignments.append("active_entities_json = %s")
        values.append(json.dumps(active_entities))
    if active_themes is not None:
        assignments.append("active_themes_json = ?")
        pg_assignments.append("active_themes_json = %s")
        values.append(json.dumps(active_themes))
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE advisor_conversations SET {', '.join(pg_assignments)} WHERE id = %s AND user_id = %s",
                    [*values, conversation_id, user_id],
                )
            conn.commit()
        finally:
            conn.close()
        return
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE advisor_conversations SET {', '.join(assignments)} WHERE id = ? AND user_id = ?",
            [*values, conversation_id, user_id],
        )
        conn.commit()
    finally:
        conn.close()


def create_advisor_message(
    *,
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
    advisor_run_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one user or assistant message to an advisor conversation."""
    from uuid import uuid4

    message_id = str(uuid4())
    now = utc_now_iso()
    metadata_json = json.dumps(metadata or {})
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO advisor_messages (
                        id, conversation_id, advisor_run_id, user_id, role, content, metadata_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (message_id, conversation_id, advisor_run_id, user_id, role, content, metadata_json, now),
                )
                row = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        return _row_to_advisor_message(row)
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO advisor_messages (
                id, conversation_id, advisor_run_id, user_id, role, content, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, conversation_id, advisor_run_id, user_id, role, content, metadata_json, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM advisor_messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_advisor_message(row)


def list_advisor_messages(conversation_id: str, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """List messages from an advisor conversation in chronological order."""
    normalized_limit = max(1, min(int(limit or 100), 200))
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM advisor_messages
                    WHERE conversation_id = %s AND user_id = %s
                    ORDER BY created_at ASC
                    LIMIT %s
                    """,
                    (conversation_id, user_id, normalized_limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_advisor_message(row) for row in rows]
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM advisor_messages
            WHERE conversation_id = ? AND user_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (conversation_id, user_id, normalized_limit),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_advisor_message(row) for row in rows]


def _row_to_advisor_run(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    try:
        conversation_id = row["conversation_id"] or ""
    except (KeyError, IndexError):
        conversation_id = ""
    return {
        "id": row["id"],
        "conversation_id": conversation_id,
        "user_id": row["user_id"],
        "query": row["query"],
        "mode": row["mode"],
        "status": row["status"],
        "plan": json.loads(row["plan_json"] or "{}"),
        "context": json.loads(row["context_json"] or "{}"),
        "coverage": json.loads(row["coverage_json"] or "{}"),
        "research_requests": json.loads(row["research_requests_json"] or "[]"),
        "answer": json.loads(row["answer_json"] or "{}"),
        "trace": json.loads(row["trace_json"] or "[]"),
        "error_message": row["error_message"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"] or "",
    }


def _row_to_advisor_conversation(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"] or "",
        "active_topic": row["active_topic"] or "",
        "active_research_run_id": row["active_research_run_id"] or "",
        "active_generic_research_id": row["active_generic_research_id"] or "",
        "active_entities": json.loads(row["active_entities_json"] or "[]"),
        "active_themes": json.loads(row["active_themes_json"] or "[]"),
        "status": row["status"] or "active",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_advisor_message(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "advisor_run_id": row["advisor_run_id"] or "",
        "user_id": row["user_id"],
        "role": row["role"],
        "content": row["content"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "created_at": row["created_at"],
    }


def upsert_user(
    *,
    email: str,
    google_sub: str,
    display_name: str = "",
    avatar_url: str = "",
) -> dict[str, Any]:
    """Create or update a Google-backed user and return the stored row."""
    if database_backend() == "postgres":
        return _upsert_user_postgres(
            email=email,
            google_sub=google_sub,
            display_name=display_name,
            avatar_url=avatar_url,
        )

    from uuid import uuid4

    now = utc_now_iso()
    conn = _connect()
    try:
        existing = conn.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE users
                SET email = ?, display_name = ?, avatar_url = ?, updated_at = ?, last_login_at = ?
                WHERE id = ?
                """,
                (email, display_name, avatar_url, now, now, existing["id"]),
            )
            user_id = existing["id"]
        else:
            user_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO users (
                    id, email, display_name, avatar_url, google_sub, created_at, updated_at, last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, email, display_name, avatar_url, google_sub, now, now, now),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_user(row)


def _upsert_user_postgres(
    *,
    email: str,
    google_sub: str,
    display_name: str = "",
    avatar_url: str = "",
) -> dict[str, Any]:
    """Postgres implementation of Google-backed user upsert."""
    from uuid import uuid4

    now = utc_now_iso()
    user_id = str(uuid4())
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (
                    id, email, display_name, avatar_url, google_sub, created_at, updated_at, last_login_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (google_sub) DO UPDATE SET
                    email = EXCLUDED.email,
                    display_name = EXCLUDED.display_name,
                    avatar_url = EXCLUDED.avatar_url,
                    updated_at = EXCLUDED.updated_at,
                    last_login_at = EXCLUDED.last_login_at
                RETURNING *
                """,
                (user_id, email, display_name, avatar_url, google_sub, now, now, now),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return _row_to_user(row)


def get_user(user_id: str) -> dict[str, Any] | None:
    """Load a user by internal id."""
    if database_backend() == "postgres":
        return _get_user_postgres(user_id)

    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_user(row)


def _get_user_postgres(user_id: str) -> dict[str, Any] | None:
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_user(row)


def _hash_session_secret(secret: str) -> str:
    """Hash a session secret before storing it."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_user_session(
    *,
    user_id: str,
    days: int = 30,
    user_agent: str = "",
    ip_address: str = "",
) -> tuple[str, dict[str, Any]]:
    """Create a revocable cookie session and return the raw token plus row."""
    from uuid import uuid4

    session_id = str(uuid4())
    secret = secrets.token_urlsafe(32)
    token = f"{session_id}.{secret}"
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(days=max(1, days))).isoformat()
    values = (
        session_id,
        user_id,
        _hash_session_secret(secret),
        expires_at,
        "",
        now,
        now,
        user_agent,
        ip_address,
    )
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_sessions (
                        id, user_id, session_secret_hash, expires_at, revoked_at,
                        created_at, last_used_at, user_agent, ip_address
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    values,
                )
                row = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        return token, _row_to_user_session(row)

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO user_sessions (
                id, user_id, session_secret_hash, expires_at, revoked_at,
                created_at, last_used_at, user_agent, ip_address
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM user_sessions WHERE id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    return token, _row_to_user_session(row)


def get_user_by_session_token(token: str) -> dict[str, Any] | None:
    """Return the session owner when a cookie session is valid."""
    session_id, sep, secret = token.partition(".")
    if not sep or not session_id or not secret:
        return None
    secret_hash = _hash_session_secret(secret)
    now = utc_now_iso()
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.* FROM user_sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.id = %s
                      AND s.session_secret_hash = %s
                      AND s.revoked_at = ''
                      AND s.expires_at > %s
                    """,
                    (session_id, secret_hash, now),
                )
                user = cur.fetchone()
                if user:
                    cur.execute("UPDATE user_sessions SET last_used_at = %s WHERE id = %s", (now, session_id))
            conn.commit()
        finally:
            conn.close()
        return _row_to_user(user) if user else None

    conn = _connect()
    try:
        user = conn.execute(
            """
            SELECT u.* FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.id = ?
              AND s.session_secret_hash = ?
              AND s.revoked_at = ''
              AND s.expires_at > ?
            """,
            (session_id, secret_hash, now),
        ).fetchone()
        if user:
            conn.execute("UPDATE user_sessions SET last_used_at = ? WHERE id = ?", (now, session_id))
            conn.commit()
    finally:
        conn.close()
    return _row_to_user(user) if user else None


def revoke_user_session(token: str) -> bool:
    """Revoke a single cookie session."""
    session_id, sep, secret = token.partition(".")
    if not sep or not session_id or not secret:
        return False
    secret_hash = _hash_session_secret(secret)
    now = utc_now_iso()
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE user_sessions
                    SET revoked_at = %s
                    WHERE id = %s AND session_secret_hash = %s AND revoked_at = ''
                    """,
                    (now, session_id, secret_hash),
                )
                changed = cur.rowcount > 0
            conn.commit()
        finally:
            conn.close()
        return changed

    conn = _connect()
    try:
        cur = conn.execute(
            """
            UPDATE user_sessions
            SET revoked_at = ?
            WHERE id = ? AND session_secret_hash = ? AND revoked_at = ''
            """,
            (now, session_id, secret_hash),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _row_to_user_session(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"] or "",
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "user_agent": row["user_agent"] or "",
        "ip_address": row["ip_address"] or "",
    }


def save_onboarding_profile(
    *,
    user_id: str,
    answers: dict[str, Any],
    financial_profile: dict[str, Any],
    risk_profile: dict[str, Any],
    investor_context: dict[str, Any],
    summary: str,
    confidence: str,
    missing_flags: list[str],
    profile_narrative: dict[str, Any] | None = None,
    profile_synthesis_source: str = "deterministic_fallback",
    profile_version: str = "v1",
) -> dict[str, Any]:
    """Persist raw onboarding answers and derived profile summaries."""
    if database_backend() == "postgres":
        return _save_onboarding_profile_postgres(
            user_id=user_id,
            answers=answers,
            financial_profile=financial_profile,
            risk_profile=risk_profile,
            investor_context=investor_context,
            summary=summary,
            confidence=confidence,
            missing_flags=missing_flags,
            profile_narrative=profile_narrative,
            profile_synthesis_source=profile_synthesis_source,
            profile_version=profile_version,
        )

    now = utc_now_iso()
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT user_id, profile_version FROM user_onboarding_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        version = _next_profile_version(existing["profile_version"] if existing else "", profile_version)
        values = (
            json.dumps(answers),
            json.dumps(financial_profile),
            json.dumps(risk_profile),
            json.dumps(investor_context),
            summary,
            confidence,
            json.dumps(missing_flags),
            json.dumps(profile_narrative or {}),
            profile_synthesis_source,
            version,
            now,
            user_id,
        )
        if existing:
            conn.execute(
                """
                UPDATE user_onboarding_profiles
                SET answers_json = ?, financial_profile_json = ?, risk_profile_json = ?,
                    investor_context_json = ?, summary = ?, confidence = ?,
                    missing_flags_json = ?, profile_narrative_json = ?,
                    profile_synthesis_source = ?, profile_version = ?, updated_at = ?
                WHERE user_id = ?
                """,
                values,
            )
        else:
            conn.execute(
                """
                INSERT INTO user_onboarding_profiles (
                    user_id, answers_json, financial_profile_json, risk_profile_json,
                    investor_context_json, summary, confidence, missing_flags_json,
                    profile_narrative_json, profile_synthesis_source, profile_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    json.dumps(answers),
                    json.dumps(financial_profile),
                    json.dumps(risk_profile),
                    json.dumps(investor_context),
                    summary,
                    confidence,
                    json.dumps(missing_flags),
                    json.dumps(profile_narrative or {}),
                    profile_synthesis_source,
                    version,
                    now,
                    now,
                ),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM user_onboarding_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_onboarding_profile(row)


def _next_profile_version(existing_version: str, default_version: str = "v1") -> str:
    """Increment profile version metadata when a saved profile is updated."""
    if not existing_version:
        return default_version
    prefix, _, number = existing_version.partition("v")
    if prefix:
        return default_version
    try:
        return f"v{int(number) + 1}"
    except ValueError:
        return default_version


def _save_onboarding_profile_postgres(
    *,
    user_id: str,
    answers: dict[str, Any],
    financial_profile: dict[str, Any],
    risk_profile: dict[str, Any],
    investor_context: dict[str, Any],
    summary: str,
    confidence: str,
    missing_flags: list[str],
    profile_narrative: dict[str, Any] | None = None,
    profile_synthesis_source: str = "deterministic_fallback",
    profile_version: str = "v1",
) -> dict[str, Any]:
    """Postgres implementation of onboarding profile upsert."""
    now = utc_now_iso()
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT profile_version FROM user_onboarding_profiles WHERE user_id = %s", (user_id,))
            existing = cur.fetchone()
            version = _next_profile_version(existing["profile_version"] if existing else "", profile_version)
            cur.execute(
                """
                INSERT INTO user_onboarding_profiles (
                    user_id, answers_json, financial_profile_json, risk_profile_json,
                    investor_context_json, summary, confidence, missing_flags_json,
                    profile_narrative_json, profile_synthesis_source, profile_version,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    answers_json = EXCLUDED.answers_json,
                    financial_profile_json = EXCLUDED.financial_profile_json,
                    risk_profile_json = EXCLUDED.risk_profile_json,
                    investor_context_json = EXCLUDED.investor_context_json,
                    summary = EXCLUDED.summary,
                    confidence = EXCLUDED.confidence,
                    missing_flags_json = EXCLUDED.missing_flags_json,
                    profile_narrative_json = EXCLUDED.profile_narrative_json,
                    profile_synthesis_source = EXCLUDED.profile_synthesis_source,
                    profile_version = EXCLUDED.profile_version,
                    updated_at = EXCLUDED.updated_at
                RETURNING *
                """,
                (
                    user_id,
                    json.dumps(answers),
                    json.dumps(financial_profile),
                    json.dumps(risk_profile),
                    json.dumps(investor_context),
                    summary,
                    confidence,
                    json.dumps(missing_flags),
                    json.dumps(profile_narrative or {}),
                    profile_synthesis_source,
                    version,
                    now,
                    now,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return _row_to_onboarding_profile(row)


def get_onboarding_profile(user_id: str) -> dict[str, Any] | None:
    """Load the user's derived onboarding profile."""
    if database_backend() == "postgres":
        return _get_onboarding_profile_postgres(user_id)

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM user_onboarding_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_onboarding_profile(row)


def _get_onboarding_profile_postgres(user_id: str) -> dict[str, Any] | None:
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM user_onboarding_profiles WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_onboarding_profile(row)


def _row_to_onboarding_profile(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "user_id": row["user_id"],
        "answers": json.loads(row["answers_json"]),
        "financial_profile": json.loads(row["financial_profile_json"]),
        "risk_profile": json.loads(row["risk_profile_json"]),
        "investor_context": json.loads(row["investor_context_json"]),
        "summary": row["summary"] or "",
        "confidence": row["confidence"] or "medium",
        "missing_flags": json.loads(row["missing_flags_json"] or "[]"),
        "profile_narrative": json.loads(row["profile_narrative_json"] or "{}"),
        "profile_synthesis_source": row["profile_synthesis_source"] or "deterministic_fallback",
        "profile_version": row["profile_version"] or "v1",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"] or "",
        "avatar_url": row["avatar_url"] or "",
        "google_sub": row["google_sub"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"] or "",
    }


def create_run(
    run_id: str,
    company_name: str,
    ticker: str,
    selected_pillars: list[str],
    user_id: str | None = None,
    max_retries: int = 1,
    budget_snapshot: dict[str, Any] | None = None,
) -> None:
    if database_backend() == "postgres":
        return _create_run_postgres(
            run_id=run_id,
            company_name=company_name,
            ticker=ticker,
            selected_pillars=selected_pillars,
            user_id=user_id,
            max_retries=max_retries,
            budget_snapshot=budget_snapshot,
        )

    now = utc_now_iso()
    profile_snapshot = build_profile_snapshot(user_id, captured_at=now) if user_id else {}
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO research_runs (
                id, company_name, ticker, selected_pillars_json, status, current_stage, current_substep,
                progress, stage_progress, activity_count, error_message, summary_json, result_json, user_id,
                profile_snapshot_json, profile_snapshot_captured_at, created_at, queued_at, max_retries,
                budget_snapshot_json, updated_at, last_activity_at, completed_at
            ) VALUES (?, ?, ?, ?, 'queued', 'queued', '', 0, 0, 0, '', '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, '')
            """,
            (
                run_id,
                company_name,
                ticker.upper(),
                json.dumps(selected_pillars),
                user_id or "",
                json.dumps(profile_snapshot),
                profile_snapshot.get("capturedAt", ""),
                now,
                now,
                int(max_retries),
                json.dumps(budget_snapshot or {}),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _create_run_postgres(
    *,
    run_id: str,
    company_name: str,
    ticker: str,
    selected_pillars: list[str],
    user_id: str | None = None,
    max_retries: int = 1,
    budget_snapshot: dict[str, Any] | None = None,
) -> None:
    now = utc_now_iso()
    profile_snapshot = build_profile_snapshot(user_id, captured_at=now) if user_id else {}
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO research_runs (
                id, company_name, ticker, selected_pillars_json, status, current_stage, current_substep,
                progress, stage_progress, activity_count, error_message, summary_json, result_json, user_id,
                    profile_snapshot_json, profile_snapshot_captured_at, created_at, queued_at, max_retries,
                    budget_snapshot_json, updated_at, last_activity_at, completed_at
                ) VALUES (%s, %s, %s, %s, 'queued', 'queued', '', 0, 0, 0, '', '', '', %s, %s, %s, %s, %s, %s, %s, %s, %s, '')
                """,
                (
                    run_id,
                    company_name,
                    ticker.upper(),
                    json.dumps(selected_pillars),
                    user_id or "",
                    json.dumps(profile_snapshot),
                    profile_snapshot.get("capturedAt", ""),
                    now,
                    now,
                    int(max_retries),
                    json.dumps(budget_snapshot or {}),
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def build_profile_snapshot(user_id: str, captured_at: str | None = None) -> dict[str, Any]:
    """Capture the personalization profile used by a research run.

    Raw onboarding answers are intentionally excluded. The snapshot stores only
    derived fields that influence recommendations.
    """
    profile = get_onboarding_profile(user_id)
    if not profile:
        return {}
    return {
        "userId": user_id,
        "financialProfile": profile["financial_profile"],
        "riskProfile": profile["risk_profile"],
        "investorContext": profile["investor_context"],
        "profileNarrative": profile["profile_narrative"],
        "profileSynthesisSource": profile["profile_synthesis_source"],
        "profileVersion": profile["profile_version"],
        "profileUpdatedAt": profile["updated_at"],
        "capturedAt": captured_at or utc_now_iso(),
    }


def append_event(run_id: str, stage: str, status: str, payload: dict[str, Any]) -> None:
    if database_backend() == "postgres":
        return _append_event_postgres(run_id, stage, status, payload)

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO run_events (run_id, stage, status, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, stage, status, json.dumps(payload), utc_now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _append_event_postgres(run_id: str, stage: str, status: str, payload: dict[str, Any]) -> None:
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_events (run_id, stage, status, payload_json, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (run_id, stage, status, json.dumps(payload), utc_now_iso()),
            )
        conn.commit()
    finally:
        conn.close()


def update_run_state(
    run_id: str,
    *,
    status: str | None = None,
    current_stage: str | None = None,
    current_substep: str | None = None,
    progress: float | None = None,
    stage_progress: float | None = None,
    activity_count: int | None = None,
    last_activity_at: str | None = None,
    error_message: str | None = None,
    summary: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    completed: bool = False,
    started_at: str | None = None,
) -> None:
    if database_backend() == "postgres":
        return _update_run_state_postgres(
            run_id,
            status=status,
            current_stage=current_stage,
            current_substep=current_substep,
            progress=progress,
            stage_progress=stage_progress,
            activity_count=activity_count,
            last_activity_at=last_activity_at,
            error_message=error_message,
            summary=summary,
            result=result,
            completed=completed,
            started_at=started_at,
        )

    fields: list[str] = ["updated_at = ?"]
    values: list[Any] = [utc_now_iso()]
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if current_stage is not None:
        fields.append("current_stage = ?")
        values.append(current_stage)
    if current_substep is not None:
        fields.append("current_substep = ?")
        values.append(current_substep)
    if progress is not None:
        fields.append("progress = ?")
        values.append(progress)
    if stage_progress is not None:
        fields.append("stage_progress = ?")
        values.append(stage_progress)
    if activity_count is not None:
        fields.append("activity_count = ?")
        values.append(activity_count)
    if last_activity_at is not None:
        fields.append("last_activity_at = ?")
        values.append(last_activity_at)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    if summary is not None:
        fields.append("summary_json = ?")
        values.append(json.dumps(summary))
    if result is not None:
        fields.append("result_json = ?")
        values.append(json.dumps(result))
    if completed:
        fields.append("completed_at = ?")
        values.append(utc_now_iso())
        fields.append("lease_owner = ?")
        values.append("")
        fields.append("lease_expires_at = ?")
        values.append("")

    values.append(run_id)
    conn = _connect()
    try:
        conn.execute(f"UPDATE research_runs SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def _update_run_state_postgres(
    run_id: str,
    *,
    status: str | None = None,
    current_stage: str | None = None,
    current_substep: str | None = None,
    progress: float | None = None,
    stage_progress: float | None = None,
    activity_count: int | None = None,
    last_activity_at: str | None = None,
    error_message: str | None = None,
    summary: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    completed: bool = False,
    started_at: str | None = None,
) -> None:
    fields: list[str] = ["updated_at = %s"]
    values: list[Any] = [utc_now_iso()]
    if status is not None:
        fields.append("status = %s")
        values.append(status)
    if current_stage is not None:
        fields.append("current_stage = %s")
        values.append(current_stage)
    if current_substep is not None:
        fields.append("current_substep = %s")
        values.append(current_substep)
    if progress is not None:
        fields.append("progress = %s")
        values.append(progress)
    if stage_progress is not None:
        fields.append("stage_progress = %s")
        values.append(stage_progress)
    if activity_count is not None:
        fields.append("activity_count = %s")
        values.append(activity_count)
    if last_activity_at is not None:
        fields.append("last_activity_at = %s")
        values.append(last_activity_at)
    if error_message is not None:
        fields.append("error_message = %s")
        values.append(error_message)
    if summary is not None:
        fields.append("summary_json = %s")
        values.append(json.dumps(summary))
    if result is not None:
        fields.append("result_json = %s")
        values.append(json.dumps(result))
    if started_at is not None:
        fields.append("started_at = %s")
        values.append(started_at)
    if completed:
        fields.append("completed_at = %s")
        values.append(utc_now_iso())
        fields.append("lease_owner = %s")
        values.append("")
        fields.append("lease_expires_at = %s")
        values.append("")

    values.append(run_id)
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE research_runs SET {', '.join(fields)} WHERE id = %s", values)
        conn.commit()
    finally:
        conn.close()


def _row_to_run(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    summary_json = row["summary_json"] or ""
    result_json = row["result_json"] or ""
    last_activity_at = row["last_activity_at"] or row["updated_at"]
    return {
        "id": row["id"],
        "user_id": row["user_id"] or "",
        "company_name": row["company_name"],
        "ticker": row["ticker"],
        "selected_pillars": json.loads(row["selected_pillars_json"]),
        "status": row["status"],
        "current_stage": row["current_stage"],
        "current_substep": row["current_substep"] or "",
        "progress": row["progress"],
        "stage_progress": row["stage_progress"] or 0,
        "activity_count": row["activity_count"] or 0,
        "error_message": row["error_message"] or "",
        "summary": json.loads(summary_json) if summary_json else None,
        "result": json.loads(result_json) if result_json else None,
        "profile_snapshot": json.loads(row["profile_snapshot_json"] or "{}"),
        "profile_snapshot_captured_at": row["profile_snapshot_captured_at"] or "",
        "queued_at": row["queued_at"] or "",
        "started_at": row["started_at"] or "",
        "failed_at": row["failed_at"] or "",
        "lease_owner": row["lease_owner"] or "",
        "lease_expires_at": row["lease_expires_at"] or "",
        "heartbeat_at": row["heartbeat_at"] or "",
        "retry_count": row["retry_count"] or 0,
        "max_retries": row["max_retries"] or 0,
        "budget_snapshot": json.loads(row["budget_snapshot_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_activity_at": last_activity_at,
        "is_stalled": _is_stalled(row["status"], last_activity_at),
        "completed_at": row["completed_at"] or "",
    }


def _is_stalled(status: str, last_activity_at: str, threshold_seconds: int = 45) -> bool:
    if status != "running" or not last_activity_at:
        return False
    try:
        activity_time = datetime.fromisoformat(last_activity_at)
    except ValueError:
        return False
    if activity_time.tzinfo is None:
        activity_time = activity_time.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - activity_time).total_seconds() > threshold_seconds


def get_run(run_id: str, user_id: str | None = None, anonymous_only: bool = False) -> dict[str, Any] | None:
    if database_backend() == "postgres":
        return _get_run_postgres(run_id, user_id=user_id, anonymous_only=anonymous_only)

    conn = _connect()
    try:
        if user_id is not None:
            row = conn.execute("SELECT * FROM research_runs WHERE id = ? AND user_id = ?", (run_id, user_id)).fetchone()
        elif anonymous_only:
            row = conn.execute("SELECT * FROM research_runs WHERE id = ? AND user_id = ''", (run_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM research_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_run(row)


def _get_run_postgres(run_id: str, user_id: str | None = None, anonymous_only: bool = False) -> dict[str, Any] | None:
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute("SELECT * FROM research_runs WHERE id = %s AND user_id = %s", (run_id, user_id))
            elif anonymous_only:
                cur.execute("SELECT * FROM research_runs WHERE id = %s AND user_id = ''", (run_id,))
            else:
                cur.execute("SELECT * FROM research_runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_run(row)


def list_runs(
    limit: int = 20,
    user_id: str | None = None,
    anonymous_only: bool = False,
    status: str | None = None,
    ticker: str | None = None,
    q: str | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if database_backend() == "postgres":
        return _list_runs_postgres(
            limit=limit,
            user_id=user_id,
            anonymous_only=anonymous_only,
            status=status,
            ticker=ticker,
            q=q,
            offset=offset,
        )

    conn = _connect()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        elif anonymous_only:
            clauses.append("user_id = ''")
        if status:
            clauses.append("status = ?")
            params.append(status)
        if ticker:
            clauses.append("UPPER(ticker) = ?")
            params.append(ticker.upper())
        if q:
            clauses.append("(LOWER(company_name) LIKE ? OR LOWER(ticker) LIKE ?)")
            like = f"%{q.lower()}%"
            params.extend([like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, max(offset, 0)])
        rows = conn.execute(
            f"SELECT * FROM research_runs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_run(row) for row in rows]


def count_user_runs_since(user_id: str, since_iso: str) -> int:
    """Count user-owned research runs created after a timestamp."""
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS count FROM research_runs WHERE user_id = %s AND created_at >= %s",
                    (user_id, since_iso),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return int(row["count"] if row else 0)

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM research_runs WHERE user_id = ? AND created_at >= ?",
            (user_id, since_iso),
        ).fetchone()
    finally:
        conn.close()
    return int(row["count"] if row else 0)


def count_active_user_runs(user_id: str) -> int:
    """Count queued/running user-owned research runs."""
    active = ("queued", "running")
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS count FROM research_runs WHERE user_id = %s AND status = ANY(%s)",
                    (user_id, list(active)),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return int(row["count"] if row else 0)

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM research_runs WHERE user_id = ? AND status IN (?, ?)",
            (user_id, *active),
        ).fetchone()
    finally:
        conn.close()
    return int(row["count"] if row else 0)


def lease_next_research_run(worker_id: str, lease_seconds: int = 300) -> dict[str, Any] | None:
    """Atomically lease the next queued or stale running research run.

    The durable worker uses this as its only admission path. A stale running run
    is eligible when its lease has expired and it has retries remaining.
    """
    now = utc_now_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(lease_seconds, 30))).isoformat()
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH candidate AS (
                        SELECT id
                        FROM research_runs
                        WHERE
                            status = 'queued'
                            OR (
                                status = 'running'
                                AND lease_expires_at <> ''
                                AND lease_expires_at < %s
                                AND retry_count < max_retries
                            )
                        ORDER BY queued_at ASC, created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE research_runs
                    SET
                        status = 'running',
                        current_stage = CASE WHEN current_stage = 'queued' THEN 'leased' ELSE current_stage END,
                        lease_owner = %s,
                        lease_expires_at = %s,
                        heartbeat_at = %s,
                        started_at = CASE WHEN started_at = '' THEN %s ELSE started_at END,
                        retry_count = CASE WHEN status = 'running' THEN retry_count + 1 ELSE retry_count END,
                        updated_at = %s,
                        last_activity_at = %s
                    WHERE id = (SELECT id FROM candidate)
                    RETURNING *
                    """,
                    (now, worker_id, expires_at, now, now, now, now),
                )
                row = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        return _row_to_run(row) if row else None

    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM research_runs
            WHERE
                status = 'queued'
                OR (
                    status = 'running'
                    AND lease_expires_at <> ''
                    AND lease_expires_at < ?
                    AND retry_count < max_retries
                )
            ORDER BY queued_at ASC, created_at ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        retry_count = int(row["retry_count"] or 0) + (1 if row["status"] == "running" else 0)
        conn.execute(
            """
            UPDATE research_runs
            SET status = 'running',
                current_stage = CASE WHEN current_stage = 'queued' THEN 'leased' ELSE current_stage END,
                lease_owner = ?,
                lease_expires_at = ?,
                heartbeat_at = ?,
                started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                retry_count = ?,
                updated_at = ?,
                last_activity_at = ?
            WHERE id = ?
            """,
            (worker_id, expires_at, now, now, retry_count, now, now, row["id"]),
        )
        saved = conn.execute("SELECT * FROM research_runs WHERE id = ?", (row["id"],)).fetchone()
        conn.commit()
    finally:
        conn.close()
    return _row_to_run(saved) if saved else None


def renew_research_run_lease(run_id: str, worker_id: str, lease_seconds: int = 300) -> bool:
    """Extend a running run lease if it is still owned by the worker."""
    now = utc_now_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(lease_seconds, 30))).isoformat()
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE research_runs
                    SET lease_expires_at = %s, heartbeat_at = %s, updated_at = %s, last_activity_at = %s
                    WHERE id = %s AND lease_owner = %s AND status = 'running'
                    """,
                    (expires_at, now, now, now, run_id, worker_id),
                )
                changed = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return bool(changed)

    conn = _connect()
    try:
        cur = conn.execute(
            """
            UPDATE research_runs
            SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ?, last_activity_at = ?
            WHERE id = ? AND lease_owner = ? AND status = 'running'
            """,
            (expires_at, now, now, now, run_id, worker_id),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def release_research_run_for_retry(run_id: str, error_message: str) -> None:
    """Return a failed leased run to the queue when retries remain, otherwise fail it."""
    run = get_run(run_id)
    if not run:
        return
    if int(run.get("retry_count", 0) or 0) < int(run.get("max_retries", 0) or 0):
        update_run_state(
            run_id,
            status="queued",
            current_stage="retry_queued",
            current_substep="waiting for retry",
            error_message=error_message,
            last_activity_at=utc_now_iso(),
        )
        _clear_research_run_lease(run_id)
        append_event(
            run_id,
            "retry_queued",
            "queued",
            {"error": error_message, "retryCount": run.get("retry_count", 0), "maxRetries": run.get("max_retries", 0)},
        )
        return
    update_run_state(
        run_id,
        status="failed",
        current_stage="failed",
        current_substep="retry limit reached",
        progress=100,
        error_message=error_message,
        last_activity_at=utc_now_iso(),
        completed=True,
    )
    _set_research_run_failed_at(run_id)


def _clear_research_run_lease(run_id: str) -> None:
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE research_runs SET lease_owner = '', lease_expires_at = '', updated_at = %s WHERE id = %s",
                    (utc_now_iso(), run_id),
                )
            conn.commit()
        finally:
            conn.close()
        return
    conn = _connect()
    try:
        conn.execute(
            "UPDATE research_runs SET lease_owner = '', lease_expires_at = '', updated_at = ? WHERE id = ?",
            (utc_now_iso(), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def _set_research_run_failed_at(run_id: str) -> None:
    if database_backend() == "postgres":
        conn = _connect_postgres()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE research_runs SET failed_at = %s WHERE id = %s AND failed_at = ''",
                    (utc_now_iso(), run_id),
                )
            conn.commit()
        finally:
            conn.close()
        return
    conn = _connect()
    try:
        conn.execute("UPDATE research_runs SET failed_at = ? WHERE id = ? AND failed_at = ''", (utc_now_iso(), run_id))
        conn.commit()
    finally:
        conn.close()


def _list_runs_postgres(
    limit: int = 20,
    user_id: str | None = None,
    anonymous_only: bool = False,
    status: str | None = None,
    ticker: str | None = None,
    q: str | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            clauses: list[str] = []
            params: list[Any] = []
            if user_id is not None:
                clauses.append("user_id = %s")
                params.append(user_id)
            elif anonymous_only:
                clauses.append("user_id = ''")
            if status:
                clauses.append("status = %s")
                params.append(status)
            if ticker:
                clauses.append("UPPER(ticker) = %s")
                params.append(ticker.upper())
            if q:
                clauses.append("(LOWER(company_name) LIKE %s OR LOWER(ticker) LIKE %s)")
                like = f"%{q.lower()}%"
                params.extend([like, like])
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.extend([limit, max(offset, 0)])
            cur.execute(f"SELECT * FROM research_runs {where} ORDER BY created_at DESC LIMIT %s OFFSET %s", params)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_run(row) for row in rows]
