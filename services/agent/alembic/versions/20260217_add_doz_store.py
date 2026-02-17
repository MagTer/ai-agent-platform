"""Add Doz Apotek store.

Revision ID: f3a8d1c9e2b4
Revises: 2127e8336d9b
Create Date: 2026-02-17
"""

from alembic import op

revision = "f3a8d1c9e2b4"
down_revision = "2127e8336d9b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO price_tracker_stores
            (id, name, slug, store_type, base_url, parser_config, is_active)
        VALUES (
            gen_random_uuid(),
            'Doz Apotek',
            'doz',
            'pharmacy',
            'https://www.dozapotek.se',
            '{}',
            true
        )
        """
    )
    op.execute(
        "ALTER TABLE price_tracker_product_stores "
        "ALTER COLUMN next_check_at TYPE TIMESTAMP WITHOUT TIME ZONE"
    )


def downgrade() -> None:
    op.execute("DELETE FROM price_tracker_stores WHERE slug = 'doz'")
    op.execute(
        "ALTER TABLE price_tracker_product_stores "
        "ALTER COLUMN next_check_at TYPE TIMESTAMP WITH TIME ZONE"
    )
