"""Add advisor harness runs.

Revision ID: 0004_advisor_runs
Revises: 0003_user_memory_graph
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_advisor_runs"
down_revision = "0003_user_memory_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advisor_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("context_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("coverage_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("research_requests_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("answer_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("trace_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("idx_advisor_runs_user_created", "advisor_runs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_advisor_runs_user_created", table_name="advisor_runs")
    op.drop_table("advisor_runs")
