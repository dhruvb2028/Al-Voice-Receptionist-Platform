"""tenant notification email

Revision ID: 7d3f1c8a2b90
Revises: 905b6bd15493
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d3f1c8a2b90"
down_revision: str | None = "905b6bd15493"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenant_config", sa.Column("notification_email", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_config", "notification_email")
