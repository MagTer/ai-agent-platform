"""Add active_context_id to users table.

Revision ID: 20260211_active_ctx
Revises: 20260210_composite_idx
Create Date: 2026-02-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260211_active_ctx"
down_revision: str | Sequence[str] | None = "20260210_composite_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add active_context_id to users table."""
    op.add_column(
        "users",
        sa.Column(
            "active_context_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contexts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_users_active_context_id", "users", ["active_context_id"])


def downgrade() -> None:
    """Remove active_context_id from users table."""
    op.drop_index("ix_users_active_context_id", table_name="users")
    op.drop_column("users", "active_context_id")
