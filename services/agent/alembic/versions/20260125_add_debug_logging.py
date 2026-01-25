"""Add system_config and debug_logs tables.

Revision ID: 20260125_debug_logging
Revises: 20260125_homey_cache
Create Date: 2026-01-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260125_debug_logging"
down_revision: str | Sequence[str] | None = "20260125_homey_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create system_config and debug_logs tables."""
    # system_config table for global configuration
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", JSONB(), nullable=False, server_default="{}"),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # debug_logs table for storing debug log entries
    op.create_table(
        "debug_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("event_data", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # Indexes for efficient lookups and cleanup
    op.create_index("ix_debug_logs_trace_id", "debug_logs", ["trace_id"])
    op.create_index("ix_debug_logs_conversation_id", "debug_logs", ["conversation_id"])
    op.create_index("ix_debug_logs_event_type", "debug_logs", ["event_type"])
    op.create_index("ix_debug_logs_created_at", "debug_logs", ["created_at"])


def downgrade() -> None:
    """Drop system_config and debug_logs tables."""
    op.drop_table("debug_logs")
    op.drop_table("system_config")
