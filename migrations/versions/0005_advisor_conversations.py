"""Add advisor conversation threads.

Revision ID: 0005_advisor_conversations
Revises: 0004_advisor_runs
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_advisor_conversations"
down_revision = "0004_advisor_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "advisor_runs",
        sa.Column("conversation_id", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "advisor_conversations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("active_topic", sa.Text(), nullable=False, server_default=""),
        sa.Column("active_research_run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("active_generic_research_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("active_entities_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("active_themes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "advisor_messages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("advisor_run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["advisor_conversations.id"]),
    )
    op.create_index("idx_advisor_runs_conversation_created", "advisor_runs", ["conversation_id", "created_at"])
    op.create_index(
        "idx_advisor_conversations_user_updated",
        "advisor_conversations",
        ["user_id", "updated_at"],
    )
    op.create_index(
        "idx_advisor_messages_conversation_created",
        "advisor_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_advisor_messages_conversation_created", table_name="advisor_messages")
    op.drop_index("idx_advisor_conversations_user_updated", table_name="advisor_conversations")
    op.drop_index("idx_advisor_runs_conversation_created", table_name="advisor_runs")
    op.drop_table("advisor_messages")
    op.drop_table("advisor_conversations")
    op.drop_column("advisor_runs", "conversation_id")
