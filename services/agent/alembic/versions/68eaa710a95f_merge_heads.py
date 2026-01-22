"""merge_heads

Revision ID: 68eaa710a95f
Revises: 20260118_oauth_user_id, 20260122_staggered_scheduling
Create Date: 2026-01-22 19:37:01.510112

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '68eaa710a95f'
down_revision: Union[str, Sequence[str], None] = ('20260118_oauth_user_id', '20260122_staggered_scheduling')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
