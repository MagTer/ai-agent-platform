"""Migrate credentials from user-scoped to context-scoped.

Removes dead credential types (github_token, gitlab_token, jira_api_token),
replaces user_id FK with context_id FK on user_credentials table.

Revision ID: 20260212_cred_ctx
Revises: 20260212_ctx_shared
Create Date: 2026-02-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260212_cred_ctx"
down_revision: str | Sequence[str] | None = "20260212_ctx_shared"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Delete dead credential types (only azure_devops_pat has a consumer)
    op.execute("DELETE FROM user_credentials WHERE credential_type != 'azure_devops_pat'")

    # 2. Add nullable context_id column
    op.add_column(
        "user_credentials",
        sa.Column(
            "context_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contexts.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )

    # 3. Data migration: map user credentials to their default context
    op.execute(
        """
        UPDATE user_credentials uc SET context_id = (
            SELECT uctx.context_id FROM user_contexts uctx
            WHERE uctx.user_id = uc.user_id AND uctx.is_default = true
            LIMIT 1
        )
        """
    )

    # Fallback: if is_default wasn't set, use any owner context
    op.execute(
        """
        UPDATE user_credentials uc SET context_id = (
            SELECT uctx.context_id FROM user_contexts uctx
            WHERE uctx.user_id = uc.user_id AND uctx.role = 'owner'
            LIMIT 1
        ) WHERE uc.context_id IS NULL
        """
    )

    # 4. Delete orphaned rows that couldn't be mapped
    op.execute("DELETE FROM user_credentials WHERE context_id IS NULL")

    # 5. Make context_id NOT NULL
    op.alter_column("user_credentials", "context_id", nullable=False)

    # 6. Drop old user_id-based constraints and column
    op.drop_constraint("uq_user_credential_type", "user_credentials", type_="unique")
    op.drop_index("ix_user_credentials_user_id", table_name="user_credentials")
    op.drop_column("user_credentials", "user_id")

    # 7. Create new context_id-based constraints
    op.create_index("ix_user_credentials_context_id", "user_credentials", ["context_id"])
    op.create_unique_constraint(
        "uq_context_credential_type",
        "user_credentials",
        ["context_id", "credential_type"],
    )


def downgrade() -> None:
    # Reverse: drop context_id constraints, add back user_id
    op.drop_constraint("uq_context_credential_type", "user_credentials", type_="unique")
    op.drop_index("ix_user_credentials_context_id", table_name="user_credentials")

    # Add user_id back (nullable first for data migration)
    op.add_column(
        "user_credentials",
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )

    # Map context_id back to user_id via user_contexts
    op.execute(
        """
        UPDATE user_credentials uc SET user_id = (
            SELECT uctx.user_id FROM user_contexts uctx
            WHERE uctx.context_id = uc.context_id AND uctx.role = 'owner'
            LIMIT 1
        )
        """
    )

    # Delete orphans and make NOT NULL
    op.execute("DELETE FROM user_credentials WHERE user_id IS NULL")
    op.alter_column("user_credentials", "user_id", nullable=False)

    # Drop context_id column
    op.drop_column("user_credentials", "context_id")

    # Recreate original constraints
    op.create_index("ix_user_credentials_user_id", "user_credentials", ["user_id"])
    op.create_unique_constraint(
        "uq_user_credential_type",
        "user_credentials",
        ["user_id", "credential_type"],
    )
