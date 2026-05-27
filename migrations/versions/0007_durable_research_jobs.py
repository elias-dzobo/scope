"""Add durable research job leasing fields.

Revision ID: 0007_durable_research_jobs
Revises: 0006_user_sessions
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_durable_research_jobs"
down_revision = "0006_user_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in [
        sa.Column("queued_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("failed_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("lease_owner", sa.Text(), nullable=False, server_default=""),
        sa.Column("lease_expires_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("heartbeat_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("budget_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
    ]:
        op.add_column("research_runs", column)
    op.create_index("idx_research_runs_lease", "research_runs", ["status", "lease_expires_at", "queued_at"])


def downgrade() -> None:
    op.drop_index("idx_research_runs_lease", table_name="research_runs")
    for column_name in [
        "budget_snapshot_json",
        "max_retries",
        "retry_count",
        "heartbeat_at",
        "lease_expires_at",
        "lease_owner",
        "failed_at",
        "started_at",
        "queued_at",
    ]:
        op.drop_column("research_runs", column_name)
