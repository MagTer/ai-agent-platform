"""add_user_id_to_oauth_tokens

Revision ID: 20260118_oauth_user_id
Revises: 20260118_user_credentials
Create Date: 2026-01-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260118_oauth_user_id"
down_revision: str | Sequence[str] | None = "20260118_user_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add user_id to oauth_tokens
    op.add_column(
        "oauth_tokens",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_oauth_tokens_user_id", "oauth_tokens", ["user_id"])
    op.create_foreign_key(
        "fk_oauth_tokens_user_id",
        "oauth_tokens",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Update unique constraint to include user_id
    op.drop_constraint("uq_context_provider", "oauth_tokens", type_="unique")
    op.create_unique_constraint(
        "uq_context_provider_user", "oauth_tokens", ["context_id", "provider", "user_id"]
    )

    # Add user_id to oauth_states
    op.add_column(
        "oauth_states",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_oauth_states_user_id",
        "oauth_states",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Remove user_id from oauth_states
    op.drop_constraint("fk_oauth_states_user_id", "oauth_states", type_="foreignkey")
    op.drop_column("oauth_states", "user_id")

    # Restore old unique constraint
    op.drop_constraint("uq_context_provider_user", "oauth_tokens", type_="unique")
    op.create_unique_constraint("uq_context_provider", "oauth_tokens", ["context_id", "provider"])

    # Remove user_id from oauth_tokens
    op.drop_constraint("fk_oauth_tokens_user_id", "oauth_tokens", type_="foreignkey")
    op.drop_index("ix_oauth_tokens_user_id", "oauth_tokens")
    op.drop_column("oauth_tokens", "user_id")
