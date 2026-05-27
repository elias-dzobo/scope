"""Initial Scope product schema.

Revision ID: 0001_initial_scope_schema
Revises:
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_scope_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("avatar_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("google_sub", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("last_login_at", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "research_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("selected_pillars_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_stage", sa.Text(), nullable=False),
        sa.Column("current_substep", sa.Text(), nullable=False, server_default=""),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("stage_progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("activity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), server_default=""),
        sa.Column("summary_json", sa.Text(), server_default=""),
        sa.Column("result_json", sa.Text(), server_default=""),
        sa.Column("profile_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("profile_snapshot_captured_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("last_activity_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("completed_at", sa.Text(), server_default=""),
    )
    op.create_index("idx_research_runs_user_created", "research_runs", ["user_id", "created_at"])

    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Text(), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_run_events_run_created", "run_events", ["run_id", "created_at"])

    op.create_table(
        "user_onboarding_profiles",
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("answers_json", sa.Text(), nullable=False),
        sa.Column("financial_profile_json", sa.Text(), nullable=False),
        sa.Column("risk_profile_json", sa.Text(), nullable=False),
        sa.Column("investor_context_json", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Text(), nullable=False, server_default="medium"),
        sa.Column("missing_flags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("profile_narrative_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("profile_synthesis_source", sa.Text(), nullable=False, server_default="deterministic_fallback"),
        sa.Column("profile_version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_onboarding_profiles")
    op.drop_index("idx_run_events_run_created", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("idx_research_runs_user_created", table_name="research_runs")
    op.drop_table("research_runs")
    op.drop_table("users")
