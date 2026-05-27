"""Add run-owned artifact manifest.

Revision ID: 0002_artifact_manifest
Revises: 0001_initial_scope_schema
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_artifact_manifest"
down_revision = "0001_initial_scope_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifact_manifest",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("run_id", sa.Text(), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("storage_backend", sa.Text(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_artifact_manifest_run", "artifact_manifest", ["run_id"])
    op.create_index("idx_artifact_manifest_user_created", "artifact_manifest", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_artifact_manifest_user_created", table_name="artifact_manifest")
    op.drop_index("idx_artifact_manifest_run", table_name="artifact_manifest")
    op.drop_table("artifact_manifest")
