"""tenant-supplied average job value

Revision ID: 8c2e5f7b91a4
Revises: 4e9b7a1c6d23
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c2e5f7b91a4"
down_revision: str | None = "4e9b7a1c6d23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column("average_job_value_cents", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "average_job_value_non_negative",
        "tenant_config",
        "average_job_value_cents IS NULL OR average_job_value_cents >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("average_job_value_non_negative", "tenant_config", type_="check")
    op.drop_column("tenant_config", "average_job_value_cents")
