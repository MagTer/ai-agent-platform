"""add_cascade_to_core_fks

Add CASCADE to foreign keys in conversations, sessions, and messages tables.
This ensures that deleting a context, conversation, or session will cascade
to child records, preventing orphaned records when using raw SQL deletes.

Revision ID: ae5be0359696
Revises: 20260212_cred_ctx
Create Date: 2026-02-13 07:38:38.203958

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ae5be0359696"
down_revision: str | Sequence[str] | None = "20260212_cred_ctx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add CASCADE to core foreign keys."""
    # 1. conversations.context_id -> contexts.id
    op.drop_constraint("conversations_context_id_fkey", "conversations", type_="foreignkey")
    op.create_foreign_key(
        "conversations_context_id_fkey",
        "conversations",
        "contexts",
        ["context_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 2. sessions.conversation_id -> conversations.id
    op.drop_constraint("sessions_conversation_id_fkey", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "sessions_conversation_id_fkey",
        "sessions",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 3. messages.session_id -> sessions.id
    op.drop_constraint("messages_session_id_fkey", "messages", type_="foreignkey")
    op.create_foreign_key(
        "messages_session_id_fkey",
        "messages",
        "sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Remove CASCADE from core foreign keys."""
    # 1. messages.session_id -> sessions.id
    op.drop_constraint("messages_session_id_fkey", "messages", type_="foreignkey")
    op.create_foreign_key(
        "messages_session_id_fkey",
        "messages",
        "sessions",
        ["session_id"],
        ["id"],
    )

    # 2. sessions.conversation_id -> conversations.id
    op.drop_constraint("sessions_conversation_id_fkey", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "sessions_conversation_id_fkey",
        "sessions",
        "conversations",
        ["conversation_id"],
        ["id"],
    )

    # 3. conversations.context_id -> contexts.id
    op.drop_constraint("conversations_context_id_fkey", "conversations", type_="foreignkey")
    op.create_foreign_key(
        "conversations_context_id_fkey",
        "conversations",
        "contexts",
        ["context_id"],
        ["id"],
    )
