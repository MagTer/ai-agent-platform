"""Add composite indices for query optimization.

Revision ID: 20260210_composite_idx
Revises: 20260209_mcp_servers
Create Date: 2026-02-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260210_composite_idx"
down_revision: str | Sequence[str] | None = "20260209_mcp_servers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create composite indices for performance optimization.

    - ix_conversation_platform_lookup: Optimizes Telegram message routing
    - ix_message_session_created: Optimizes retention queries
    """
    # Composite index for conversation lookup by platform
    op.create_index(
        "ix_conversation_platform_lookup",
        "conversations",
        ["platform", "platform_id"],
    )

    # Composite index for message retention queries
    op.create_index(
        "ix_message_session_created",
        "messages",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    """Drop composite indices."""
    op.drop_index("ix_message_session_created", table_name="messages")
    op.drop_index("ix_conversation_platform_lookup", table_name="conversations")
