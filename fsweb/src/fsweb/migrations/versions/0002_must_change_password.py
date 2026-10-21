"""accounts without the CLI: users given a one-time or generated password must change it before anything else

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Batch mode: a plain ALTER on PostgreSQL, ADD COLUMN on SQLite (the desktop edition); existing users are unaffected.
    with op.batch_alter_table('app_user') as batch:
        batch.add_column(sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('app_user') as batch:
        batch.drop_column('must_change_password')
