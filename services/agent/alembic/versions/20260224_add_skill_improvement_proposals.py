"""Add skill_improvement_proposals table.

Revision ID: 20260224_skill_proposals
Revises: 20260222_display_name_contexts
Create Date: 2026-02-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260224_skill_proposals"
down_revision: str | None = "20260222_display_name_contexts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create skill_improvement_proposals table."""
    op.create_table(
        "skill_improvement_proposals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "context_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contexts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("skill_name", sa.String(), nullable=False, index=True),
        sa.Column("skill_file_name", sa.String(), nullable=False),
        sa.Column("original_content", sa.String(), nullable=False),
        sa.Column("proposed_content", sa.String(), nullable=False),
        sa.Column("change_summary", sa.String(), nullable=False),
        sa.Column("failure_signals", JSONB(), server_default="[]"),
        sa.Column("total_executions", sa.Integer(), server_default="0"),
        sa.Column("failed_executions", sa.Integer(), server_default="0"),
        sa.Column(
            "status",
            sa.String(),
            server_default="applied",
            nullable=False,
            index=True,
        ),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_proposal_context_skill",
        "skill_improvement_proposals",
        ["context_id", "skill_name"],
    )


def downgrade() -> None:
    """Drop skill_improvement_proposals table."""
    op.drop_index("ix_proposal_context_skill", table_name="skill_improvement_proposals")
    op.drop_table("skill_improvement_proposals")
