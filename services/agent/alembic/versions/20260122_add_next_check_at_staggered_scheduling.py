"""add_next_check_at_staggered_scheduling

Revision ID: 20260122_staggered_scheduling
Revises: 20260118_add_user_credentials
Create Date: 2026-01-22 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260122_staggered_scheduling"
down_revision: str | Sequence[str] | None = "20260118_user_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add next_check_at column
    op.add_column(
        "price_tracker_product_stores",
        sa.Column("next_check_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # Create partial index for active products with next_check_at
    op.create_index(
        "idx_product_stores_next_check_at",
        "price_tracker_product_stores",
        ["next_check_at"],
        unique=False,
        postgresql_where=sa.text("is_active = true"),
    )

    # Initialize existing records with jittered values
    # next_check_at = COALESCE(last_checked_at, NOW()) + check_frequency_hours
    #                 + random jitter (±10%)
    op.execute(
        """
        UPDATE price_tracker_product_stores
        SET next_check_at = COALESCE(last_checked_at, NOW())
                          + (check_frequency_hours * INTERVAL '1 hour')
                          + ((RANDOM() * 0.2 - 0.1) * check_frequency_hours * INTERVAL '1 hour')
        WHERE next_check_at IS NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop index first
    op.drop_index("idx_product_stores_next_check_at", table_name="price_tracker_product_stores")

    # Drop column
    op.drop_column("price_tracker_product_stores", "next_check_at")
