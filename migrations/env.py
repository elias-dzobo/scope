"""Alembic environment for Scope database migrations."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from research_core.storage import db_path

config = context.config
target_metadata = None


def _database_url() -> str:
    """Resolve the migration URL from Scope database env vars."""
    backend = os.getenv("SCOPE_DB_BACKEND", "sqlite").strip().lower() or "sqlite"
    if backend == "postgres":
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            raise RuntimeError("DATABASE_URL is required when SCOPE_DB_BACKEND=postgres")
        # SQLAlchemy defaults to psycopg2 for `postgresql://`. We ship psycopg
        # (v3), so force the correct dialect prefix.
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1).replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        return url
    path = Path(os.getenv("SCOPE_DB_PATH", str(db_path())))
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def run_migrations_offline() -> None:
    """Run migrations without creating an Engine."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live DB connection."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
