"""Add pricing and usage ledger foundation.

Revision ID: 0008_pricing_usage_foundation
Revises: 0007_durable_research_jobs
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_pricing_usage_foundation"
down_revision = "0007_durable_research_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("monthly_included_credits", sa.Float(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "user_entitlements",
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("plan_id", sa.Text(), nullable=False, server_default="free"),
        sa.Column("billing_provider", sa.Text(), nullable=False, server_default="local"),
        sa.Column("provider_customer_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider_subscription_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("subscription_status", sa.Text(), nullable=False, server_default="inactive"),
        sa.Column("credit_balance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("monthly_credit_allowance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("current_period_started_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("current_period_ends_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("advisor_run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("credits_delta", sa.Float(), nullable=False),
        sa.Column("raw_cost_estimate_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("idx_usage_ledger_user_created", "usage_ledger", ["user_id", "created_at"])
    op.create_table(
        "credit_reservations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("advisor_run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("credits_reserved", sa.Float(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="reserved"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("finalized_at", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("idx_credit_reservations_user_status", "credit_reservations", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_credit_reservations_user_status", table_name="credit_reservations")
    op.drop_table("credit_reservations")
    op.drop_index("idx_usage_ledger_user_created", table_name="usage_ledger")
    op.drop_table("usage_ledger")
    op.drop_table("user_entitlements")
    op.drop_table("billing_plans")
