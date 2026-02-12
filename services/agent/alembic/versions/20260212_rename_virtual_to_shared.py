"""Rename context type 'virtual' to 'shared'.

Revision ID: 20260212_ctx_shared
Revises: 20260211_active_ctx
Create Date: 2026-02-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260212_ctx_shared"
down_revision: str | Sequence[str] | None = "20260211_active_ctx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename virtual context type to shared."""
    op.execute("UPDATE contexts SET type = 'shared' WHERE type = 'virtual'")


def downgrade() -> None:
    """Revert shared context type back to virtual."""
    op.execute("UPDATE contexts SET type = 'virtual' WHERE type = 'shared'")
