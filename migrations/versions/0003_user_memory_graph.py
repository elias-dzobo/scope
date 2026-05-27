"""Add user memory graph tables.

Revision ID: 0003_user_memory_graph
Revises: 0002_artifact_manifest
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_user_memory_graph"
down_revision = "0002_artifact_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_nodes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("node_type", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("properties_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source_ref_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_memory_nodes_user_type", "memory_nodes", ["user_id", "node_type"])
    op.create_index(
        "idx_memory_nodes_user_type_external",
        "memory_nodes",
        ["user_id", "node_type", "external_id"],
        unique=True,
    )

    op.create_table(
        "memory_edges",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("source_node_id", sa.Text(), sa.ForeignKey("memory_nodes.id"), nullable=False),
        sa.Column("target_node_id", sa.Text(), sa.ForeignKey("memory_nodes.id"), nullable=False),
        sa.Column("edge_type", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("properties_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source_ref_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_memory_edges_user_type", "memory_edges", ["user_id", "edge_type"])
    op.create_index("idx_memory_edges_source", "memory_edges", ["source_node_id"])
    op.create_index("idx_memory_edges_target", "memory_edges", ["target_node_id"])

    op.create_table(
        "memory_chunks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("node_id", sa.Text(), sa.ForeignKey("memory_nodes.id"), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_memory_chunks_user_source", "memory_chunks", ["user_id", "source_type", "source_id"])


def downgrade() -> None:
    op.drop_index("idx_memory_chunks_user_source", table_name="memory_chunks")
    op.drop_table("memory_chunks")
    op.drop_index("idx_memory_edges_target", table_name="memory_edges")
    op.drop_index("idx_memory_edges_source", table_name="memory_edges")
    op.drop_index("idx_memory_edges_user_type", table_name="memory_edges")
    op.drop_table("memory_edges")
    op.drop_index("idx_memory_nodes_user_type_external", table_name="memory_nodes")
    op.drop_index("idx_memory_nodes_user_type", table_name="memory_nodes")
    op.drop_table("memory_nodes")
