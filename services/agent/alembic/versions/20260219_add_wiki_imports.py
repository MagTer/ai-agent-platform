"""Add wiki_imports table.

Revision ID: 20260219_wiki_imports
Revises: b2c3d4e5f6a7
Create Date: 2026-02-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260219_wiki_imports"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wiki_imports",
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
        sa.Column("wiki_identifier", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'idle'")),
        sa.Column("total_pages", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pages_imported", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_import_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_import_completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("context_id", "wiki_identifier", name="uq_context_wiki_import"),
    )


def downgrade() -> None:
    op.drop_table("wiki_imports")
