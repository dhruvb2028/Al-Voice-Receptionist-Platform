"""extend guardrail types

Revision ID: c1f2a9d0e4b7
Revises: 2b546cedf212
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c1f2a9d0e4b7"
down_revision: str | None = "2b546cedf212"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_VALUES = (
    "booking_confirmation",
    "emergency",
    "human_request",
    "system_error",
    "intent_failure",
    "max_duration",
)


def upgrade() -> None:
    # Enum additions are append-only (PG 12+ allows ADD VALUE inside a
    # transaction as long as the value is not used in the same one).
    for value in NEW_VALUES:
        op.execute(f"ALTER TYPE guardrail_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # IRREVERSIBLE: PostgreSQL cannot remove a value from an enum type,
    # and rows already using the new values would block a manual type
    # rebuild. Rolling back past this revision means restoring from a
    # backup rather than downgrading — see docs/rollback.md.
    pass
