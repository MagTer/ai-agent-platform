"""add_price_drop_threshold

Revision ID: 20260115000001
Revises: d3d249838bf6
Create Date: 2026-01-15 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260115000001"
down_revision: str | Sequence[str] | None = "d3d249838bf6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add price_drop_threshold_percent column to price_tracker_watches."""
    op.add_column(
        "price_tracker_watches",
        sa.Column("price_drop_threshold_percent", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Remove price_drop_threshold_percent column from price_tracker_watches."""
    op.drop_column("price_tracker_watches", "price_drop_threshold_percent")
