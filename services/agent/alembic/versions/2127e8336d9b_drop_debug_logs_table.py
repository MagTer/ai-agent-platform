"""drop_debug_logs_table

Revision ID: 2127e8336d9b
Revises: 20260213_sched_jobs
Create Date: 2026-02-14 08:48:09.012130

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2127e8336d9b"
down_revision: str | Sequence[str] | None = "20260213_sched_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop debug_logs table - debug events now stored in JSONL file."""
    # Drop indexes first
    op.drop_index("ix_debug_logs_trace_id", table_name="debug_logs")
    op.drop_index("ix_debug_logs_conversation_id", table_name="debug_logs")
    op.drop_index("ix_debug_logs_event_type", table_name="debug_logs")
    op.drop_index("ix_debug_logs_created_at", table_name="debug_logs")

    # Drop the table
    op.drop_table("debug_logs")


def downgrade() -> None:
    """Recreate debug_logs table for rollback."""
    from sqlalchemy.dialects import postgresql

    op.create_table(
        "debug_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("event_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Recreate indexes
    op.create_index("ix_debug_logs_trace_id", "debug_logs", ["trace_id"])
    op.create_index("ix_debug_logs_conversation_id", "debug_logs", ["conversation_id"])
    op.create_index("ix_debug_logs_event_type", "debug_logs", ["event_type"])
    op.create_index("ix_debug_logs_created_at", "debug_logs", ["created_at"])
