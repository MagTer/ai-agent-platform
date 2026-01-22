"""merge_heads

Revision ID: f12c8c12fd61
Revises: 68eaa710a95f
Create Date: 2026-01-22 19:46:05.617683

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "f12c8c12fd61"
down_revision: str | Sequence[str] | None = "68eaa710a95f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
