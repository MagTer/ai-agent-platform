"""add_check_weekday_to_product_stores

Revision ID: 6c56619b546d
Revises: f12c8c12fd61
Create Date: 2026-01-23 20:07:49.070044

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6c56619b546d"
down_revision: str | Sequence[str] | None = "f12c8c12fd61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "price_tracker_product_stores",
        sa.Column("check_weekday", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("price_tracker_product_stores", "check_weekday")
