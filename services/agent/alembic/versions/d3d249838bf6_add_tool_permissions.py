"""add_tool_permissions

Revision ID: d3d249838bf6
Revises: c2c138727ae5
Create Date: 2026-01-05 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d3d249838bf6"
down_revision: str | Sequence[str] | None = "c2c138727ae5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create tool_permissions table
    op.create_table(
        "tool_permissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "context_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("allowed", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["context_id"],
            ["contexts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("context_id", "tool_name", name="uq_context_tool"),
    )
    op.create_index(
        op.f("ix_tool_permissions_context_id"),
        "tool_permissions",
        ["context_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tool_permissions_tool_name"),
        "tool_permissions",
        ["tool_name"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_tool_permissions_tool_name"), table_name="tool_permissions")
    op.drop_index(op.f("ix_tool_permissions_context_id"), table_name="tool_permissions")
    op.drop_table("tool_permissions")
