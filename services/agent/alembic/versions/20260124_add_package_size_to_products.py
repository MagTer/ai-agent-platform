"""Add package_size and package_quantity to price_tracker_products.

Revision ID: 20260124_package_size
Revises: 20260123_context_products
Create Date: 2026-01-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260124_package_size"
down_revision: str | Sequence[str] | None = "20260123_context_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add package size columns to products table."""
    op.add_column(
        "price_tracker_products",
        sa.Column("package_size", sa.String(50), nullable=True),
    )
    op.add_column(
        "price_tracker_products",
        sa.Column("package_quantity", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    """Remove package size columns."""
    op.drop_column("price_tracker_products", "package_quantity")
    op.drop_column("price_tracker_products", "package_size")
