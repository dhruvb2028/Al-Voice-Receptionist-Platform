"""message review state and internal note

Revision ID: 4e9b7a1c6d23
Revises: 7d3f1c8a2b90
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e9b7a1c6d23"
down_revision: str | None = "7d3f1c8a2b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("internal_note_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "internal_note_encrypted")
    op.drop_column("messages", "reviewed_at")
