"""Add homey_device_cache table.

Revision ID: 20260125_homey_cache
Revises: 20260124_package_size
Create Date: 2026-01-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260125_homey_cache"
down_revision: str | Sequence[str] | None = "20260124_package_size"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create homey_device_cache table."""
    op.create_table(
        "homey_device_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "context_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contexts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("homey_id", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("device_class", sa.String(), nullable=False),
        sa.Column("capabilities", JSONB(), nullable=False, server_default="[]"),
        sa.Column("zone", sa.String(), nullable=True),
        sa.Column("cached_at", sa.DateTime(), nullable=False),
    )

    # Indexes for efficient lookups
    op.create_index("ix_homey_device_cache_context_id", "homey_device_cache", ["context_id"])
    op.create_index("ix_homey_device_cache_homey_id", "homey_device_cache", ["homey_id"])
    op.create_index("ix_homey_device_cache_device_id", "homey_device_cache", ["device_id"])

    # Unique constraint for upsert operations
    op.create_unique_constraint(
        "uq_homey_device_cache",
        "homey_device_cache",
        ["context_id", "homey_id", "device_id"],
    )


def downgrade() -> None:
    """Drop homey_device_cache table."""
    op.drop_table("homey_device_cache")
