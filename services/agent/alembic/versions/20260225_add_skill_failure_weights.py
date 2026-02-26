"""Add skill_failure_weights table.

Revision ID: 20260225_skill_fail_wt
Revises: 20260224_skill_proposals
Create Date: 2026-02-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260225_skill_fail_wt"
down_revision: str | None = "20260224_skill_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create skill_failure_weights table."""
    op.create_table(
        "skill_failure_weights",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "context_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contexts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("skill_name", sa.String(), nullable=False, index=True),
        sa.Column(
            "accumulated_weight",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
        sa.Column("failure_signals", JSONB(), server_default="[]"),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_context_skill_weight",
        "skill_failure_weights",
        ["context_id", "skill_name"],
    )


def downgrade() -> None:
    """Drop skill_failure_weights table."""
    op.drop_constraint(
        "uq_context_skill_weight",
        "skill_failure_weights",
        type_="unique",
    )
    op.drop_table("skill_failure_weights")
