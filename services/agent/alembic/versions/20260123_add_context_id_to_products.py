"""add_context_id_to_products

Revision ID: 20260123_context_products
Revises: 6c56619b546d
Create Date: 2026-01-23 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260123_context_products"
down_revision: str | Sequence[str] | None = "6c56619b546d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add context_id to products table for multi-tenancy."""
    # Add column as nullable first
    op.add_column(
        "price_tracker_products",
        sa.Column("context_id", sa.UUID(), nullable=True),
    )

    # Migrate existing products: assign context_id from their watches
    # If a product has watches, use the context_id from the first watch
    op.execute(
        """
        UPDATE price_tracker_products p
        SET context_id = (
            SELECT w.context_id
            FROM price_tracker_watches w
            WHERE w.product_id = p.id
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM price_tracker_watches w WHERE w.product_id = p.id
        )
    """
    )

    # For any products without watches, we need to handle them
    # In this case, we'll delete them as orphan products
    op.execute(
        """
        DELETE FROM price_tracker_products
        WHERE context_id IS NULL
    """
    )

    # Now make the column NOT NULL
    op.alter_column(
        "price_tracker_products",
        "context_id",
        nullable=False,
    )

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_products_context_id",
        "price_tracker_products",
        "contexts",
        ["context_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Add index for performance
    op.create_index(
        "ix_price_tracker_products_context_id",
        "price_tracker_products",
        ["context_id"],
    )


def downgrade() -> None:
    """Remove context_id from products table."""
    op.drop_index("ix_price_tracker_products_context_id", "price_tracker_products")
    op.drop_constraint("fk_products_context_id", "price_tracker_products", type_="foreignkey")
    op.drop_column("price_tracker_products", "context_id")
