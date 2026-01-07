"""add_oauth_tokens_and_states

Revision ID: c2c138727ae5
Revises: 0255b3157905
Create Date: 2026-01-05 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c2c138727ae5"
down_revision: str | Sequence[str] | None = "0255b3157905"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create oauth_tokens table
    op.create_table(
        "oauth_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("context_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("access_token", sa.String(), nullable=False),
        sa.Column("refresh_token", sa.String(), nullable=True),
        sa.Column("token_type", sa.String(), server_default="Bearer", nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("scope", sa.String(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["context_id"], ["contexts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("context_id", "provider", name="uq_context_provider"),
    )
    op.create_index(
        op.f("ix_oauth_tokens_context_id"), "oauth_tokens", ["context_id"], unique=False
    )
    op.create_index(op.f("ix_oauth_tokens_provider"), "oauth_tokens", ["provider"], unique=False)

    # Create oauth_states table
    op.create_table(
        "oauth_states",
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("context_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("code_verifier", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["context_id"], ["contexts.id"]),
        sa.PrimaryKeyConstraint("state"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("oauth_states")
    op.drop_index(op.f("ix_oauth_tokens_provider"), table_name="oauth_tokens")
    op.drop_index(op.f("ix_oauth_tokens_context_id"), table_name="oauth_tokens")
    op.drop_table("oauth_tokens")
