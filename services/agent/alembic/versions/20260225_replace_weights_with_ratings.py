"""Replace skill_failure_weights with skill_quality_ratings.

Revision ID: 20260225_replace_weights_with_ratings
Revises: 20260225_skill_failure_weights
Create Date: 2026-02-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260225_replace_weights_with_ratings"
down_revision: str | None = "20260225_skill_failure_weights"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop skill_failure_weights, create skill_quality_ratings."""
    # Drop old table
    op.drop_constraint(
        "uq_context_skill_weight",
        "skill_failure_weights",
        type_="unique",
    )
    op.drop_table("skill_failure_weights")

    # Create new table
    op.create_table(
        "skill_quality_ratings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "context_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contexts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("skill_name", sa.String(), nullable=False),
        sa.Column("functional_score", sa.Integer(), nullable=False),
        sa.Column("formatting_score", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_rating_context_skill",
        "skill_quality_ratings",
        ["context_id", "skill_name"],
    )


def downgrade() -> None:
    """Reverse: drop skill_quality_ratings, recreate skill_failure_weights."""
    op.drop_index("ix_rating_context_skill", table_name="skill_quality_ratings")
    op.drop_table("skill_quality_ratings")

    # Recreate the old table (from 20260225_skill_failure_weights migration)
    from sqlalchemy.dialects.postgresql import JSONB

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
