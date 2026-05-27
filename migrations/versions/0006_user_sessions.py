"""Add revocable user sessions.

Revision ID: 0006_user_sessions
Revises: 0005_advisor_conversations
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_user_sessions"
down_revision = "0005_advisor_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("session_secret_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
        sa.Column("ip_address", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("idx_user_sessions_user_expires", "user_sessions", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("idx_user_sessions_user_expires", table_name="user_sessions")
    op.drop_table("user_sessions")
