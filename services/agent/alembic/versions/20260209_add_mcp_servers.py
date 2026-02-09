"""Add mcp_servers table for user-managed MCP connections.

Revision ID: 20260209_mcp_servers
Revises: 20260207_workspaces
Create Date: 2026-02-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260209_mcp_servers"
down_revision: str | Sequence[str] | None = "20260207_workspaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create mcp_servers table."""
    op.create_table(
        "mcp_servers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "context_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contexts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("transport", sa.String(), nullable=False, server_default="auto"),
        sa.Column("auth_type", sa.String(), nullable=False, server_default="none"),
        sa.Column("auth_token_encrypted", sa.String(), nullable=True),
        sa.Column("oauth_provider_name", sa.String(), nullable=True),
        sa.Column("oauth_authorize_url", sa.String(), nullable=True),
        sa.Column("oauth_token_url", sa.String(), nullable=True),
        sa.Column("oauth_client_id", sa.String(), nullable=True),
        sa.Column("oauth_client_secret_encrypted", sa.String(), nullable=True),
        sa.Column("oauth_scopes", sa.String(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_connected_at", sa.DateTime(), nullable=True),
        sa.Column("tools_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # Indexes
    op.create_index("ix_mcp_servers_context_id", "mcp_servers", ["context_id"])
    op.create_index("ix_mcp_servers_name", "mcp_servers", ["name"])

    # Unique constraint: one name per context
    op.create_unique_constraint(
        "uq_context_mcp_name",
        "mcp_servers",
        ["context_id", "name"],
    )


def downgrade() -> None:
    """Drop mcp_servers table."""
    op.drop_table("mcp_servers")
