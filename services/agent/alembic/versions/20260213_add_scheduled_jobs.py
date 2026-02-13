"""Add scheduled_jobs table.

Revision ID: 20260213_sched_jobs
Revises: ae5be0359696
Create Date: 2026-02-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260213_sched_jobs"
down_revision: str | Sequence[str] | None = "ae5be0359696"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create scheduled_jobs table."""
    op.create_table(
        "scheduled_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "context_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contexts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(), nullable=False, index=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("cron_expression", sa.String(), nullable=False),
        sa.Column("skill_prompt", sa.String(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("notification_channel", sa.String(), nullable=True),
        sa.Column("notification_target", sa.String(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_status", sa.String(), nullable=True),
        sa.Column("last_run_result", sa.String(), nullable=True),
        sa.Column("last_run_duration_ms", sa.Integer(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True, index=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("context_id", "name", name="uq_context_scheduled_job_name"),
    )


def downgrade() -> None:
    """Drop scheduled_jobs table."""
    op.drop_table("scheduled_jobs")
