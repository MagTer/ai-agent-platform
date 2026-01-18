"""add_price_tracker_tables

Revision ID: 20260115_price_tracker
Revises: d3d249838bf6
Create Date: 2026-01-15 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260115_price_tracker"
down_revision: str | Sequence[str] | None = "d3d249838bf6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create price_tracker_stores table
    op.create_table(
        "price_tracker_stores",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("store_type", sa.String(20), nullable=False),
        sa.Column("base_url", sa.String(255), nullable=False),
        sa.Column(
            "parser_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_store_slug"),
    )
    op.create_index(
        op.f("ix_price_tracker_stores_slug"), "price_tracker_stores", ["slug"], unique=True
    )

    # Create price_tracker_products table
    op.create_table(
        "price_tracker_products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("brand", sa.String(100), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create price_tracker_product_stores table (many-to-many with metadata)
    op.create_table(
        "price_tracker_product_stores",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_url", sa.String(512), nullable=False),
        sa.Column("store_product_id", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("check_frequency_hours", sa.Integer(), server_default="24", nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["price_tracker_products.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["price_tracker_stores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "store_id", name="uq_product_store"),
    )
    op.create_index(
        op.f("ix_price_tracker_product_stores_product_id"),
        "price_tracker_product_stores",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_price_tracker_product_stores_store_id"),
        "price_tracker_product_stores",
        ["store_id"],
        unique=False,
    )

    # Create price_tracker_price_points table
    op.create_table(
        "price_tracker_price_points",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("product_store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("price_sek", sa.Numeric(10, 2), nullable=False),
        sa.Column("unit_price_sek", sa.Numeric(10, 2), nullable=True),
        sa.Column("offer_price_sek", sa.Numeric(10, 2), nullable=True),
        sa.Column("offer_type", sa.String(50), nullable=True),
        sa.Column("offer_details", sa.String(255), nullable=True),
        sa.Column("in_stock", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_store_id"], ["price_tracker_product_stores.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_price_tracker_price_points_product_store_id"),
        "price_tracker_price_points",
        ["product_store_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_price_tracker_price_points_checked_at"),
        "price_tracker_price_points",
        ["checked_at"],
        unique=False,
    )

    # Create price_tracker_watches table
    op.create_table(
        "price_tracker_watches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("context_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_price_sek", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "alert_on_any_offer",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("email_address", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_alerted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["context_id"], ["contexts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["price_tracker_products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_price_tracker_watches_context_id"),
        "price_tracker_watches",
        ["context_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_price_tracker_watches_product_id"),
        "price_tracker_watches",
        ["product_id"],
        unique=False,
    )

    # Seed initial stores
    op.execute(
        """
        INSERT INTO price_tracker_stores (
            name, slug, store_type, base_url, parser_config, is_active
        )
        VALUES
            ('ICA', 'ica', 'grocery', 'https://handlaprivatkund.ica.se', '{}', true),
            ('Willys', 'willys', 'grocery', 'https://www.willys.se', '{}', true),
            ('Apotea', 'apotea', 'pharmacy', 'https://www.apotea.se', '{}', true),
            ('Med24', 'med24', 'pharmacy', 'https://www.med24.se', '{}', true)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_price_tracker_watches_product_id"), table_name="price_tracker_watches")
    op.drop_index(op.f("ix_price_tracker_watches_context_id"), table_name="price_tracker_watches")
    op.drop_table("price_tracker_watches")

    op.drop_index(
        op.f("ix_price_tracker_price_points_checked_at"), table_name="price_tracker_price_points"
    )
    op.drop_index(
        op.f("ix_price_tracker_price_points_product_store_id"),
        table_name="price_tracker_price_points",
    )
    op.drop_table("price_tracker_price_points")

    op.drop_index(
        op.f("ix_price_tracker_product_stores_store_id"),
        table_name="price_tracker_product_stores",
    )
    op.drop_index(
        op.f("ix_price_tracker_product_stores_product_id"),
        table_name="price_tracker_product_stores",
    )
    op.drop_table("price_tracker_product_stores")

    op.drop_table("price_tracker_products")

    op.drop_index(op.f("ix_price_tracker_stores_slug"), table_name="price_tracker_stores")
    op.drop_table("price_tracker_stores")
