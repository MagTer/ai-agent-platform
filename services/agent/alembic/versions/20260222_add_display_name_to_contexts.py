"""Add display_name column to contexts table.

Revision ID: 20260222_display_name_contexts
Revises: 20260221_ado_team_configs
Create Date: 2026-02-22 14:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260222_display_name_contexts"
down_revision: str | None = "20260221_ado_team_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add display_name column to contexts table."""
    op.add_column(
        "contexts",
        sa.Column("display_name", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Remove display_name column from contexts table."""
    op.drop_column("contexts", "display_name")
