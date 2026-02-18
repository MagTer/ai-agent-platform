"""Rename personal contexts from 'personal_{uuid}' to 'Personal - {email}'.

Revision ID: a1b2c3d4e5f6
Revises: f3a8d1c9e2b4
Create Date: 2026-02-18
"""

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f3a8d1c9e2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rename personal contexts to include owner email."""
    # Update personal contexts that have owner_email in config
    # New format: "Personal - user@example.com"
    op.execute(
        """
        UPDATE contexts
        SET name = 'Personal - ' || (config->>'owner_email')
        WHERE type = 'personal'
          AND config->>'owner_email' IS NOT NULL
          AND name LIKE 'personal\\_%'
        """
    )


def downgrade() -> None:
    """Revert to UUID-based naming (best effort -- uses context id)."""
    op.execute(
        """
        UPDATE contexts
        SET name = 'personal_' || id::text
        WHERE type = 'personal'
          AND name LIKE 'Personal - %'
        """
    )
