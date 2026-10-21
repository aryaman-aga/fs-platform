"""backup_log.status: room for "pending_approval" (restore requests), which does not fit 12 characters.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_log") as batch:
        batch.alter_column("status", existing_type=sa.String(12), type_=sa.String(20), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("backup_log") as batch:
        batch.alter_column("status", existing_type=sa.String(20), type_=sa.String(12), existing_nullable=False)
