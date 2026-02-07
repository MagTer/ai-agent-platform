"""Add workspaces table.

Revision ID: 20260207_workspaces
Revises: 20260125_debug_logging
Create Date: 2026-02-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260207_workspaces"
down_revision: str | Sequence[str] | None = "20260125_debug_logging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create workspaces table."""
    op.create_table(
        "workspaces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "context_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contexts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("repo_url", sa.String(), nullable=False),
        sa.Column("branch", sa.String(), nullable=False, server_default="main"),
        sa.Column("local_path", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("sync_error", sa.String(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # Indexes
    op.create_index("ix_workspaces_context_id", "workspaces", ["context_id"])
    op.create_index("ix_workspaces_name", "workspaces", ["name"])

    # Unique constraints (match model __table_args__)
    op.create_unique_constraint(
        "uq_context_workspace_name",
        "workspaces",
        ["context_id", "name"],
    )
    op.create_unique_constraint(
        "uq_context_workspace_repo",
        "workspaces",
        ["context_id", "repo_url"],
    )


def downgrade() -> None:
    """Drop workspaces table."""
    op.drop_table("workspaces")
