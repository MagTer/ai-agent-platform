"""Add ado_team_configs table for DB-backed ADO team mappings.

Revision ID: 20260221_ado_team_configs
Revises: 20260219_wiki_imports
Create Date: 2026-02-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260221_ado_team_configs"
down_revision: str | Sequence[str] | None = "20260219_wiki_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ado_team_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # NULL only for the global-defaults row (is_default=TRUE)
        sa.Column("alias", sa.String(), nullable=True, unique=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("area_path", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column("default_type", sa.String(), nullable=False),
        sa.Column(
            "default_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ado_team_configs_is_default", "ado_team_configs", ["is_default"])
    op.create_index("ix_ado_team_configs_sort_order", "ado_team_configs", ["sort_order"])


def downgrade() -> None:
    op.drop_index("ix_ado_team_configs_sort_order", table_name="ado_team_configs")
    op.drop_index("ix_ado_team_configs_is_default", table_name="ado_team_configs")
    op.drop_table("ado_team_configs")
