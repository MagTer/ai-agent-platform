"""Add ON DELETE CASCADE to conversations.context_id FK.

Every other FK to contexts.id already has CASCADE. This was an oversight
that prevented deleting contexts with conversations.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-18
"""

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add CASCADE to conversations.context_id foreign key."""
    op.drop_constraint("conversations_context_id_fkey", "conversations", type_="foreignkey")
    op.create_foreign_key(
        "conversations_context_id_fkey",
        "conversations",
        "contexts",
        ["context_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Revert to RESTRICT (no ondelete clause)."""
    op.drop_constraint("conversations_context_id_fkey", "conversations", type_="foreignkey")
    op.create_foreign_key(
        "conversations_context_id_fkey",
        "conversations",
        "contexts",
        ["context_id"],
        ["id"],
    )
