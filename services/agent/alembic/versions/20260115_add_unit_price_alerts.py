"""add_unit_price_alerts

Revision ID: 20260115000002
Revises: 20260115000001
Create Date: 2026-01-15 00:00:02.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260115000002"
down_revision: str | Sequence[str] | None = "20260115000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add unit price alert fields to price_tracker_watches."""
    op.add_column(
        "price_tracker_watches",
        sa.Column("unit_price_target_sek", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "price_tracker_watches",
        sa.Column("unit_price_drop_threshold_percent", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Remove unit price alert fields from price_tracker_watches."""
    op.drop_column("price_tracker_watches", "unit_price_drop_threshold_percent")
    op.drop_column("price_tracker_watches", "unit_price_target_sek")
