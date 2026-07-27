"""notification preferences, deliveries, consent, and suppressions

Revision ID: 9f4a2d8e5c17
Revises: 8c2e5f7b91a4
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9f4a2d8e5c17"
down_revision: str | None = "8c2e5f7b91a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: the types are created once, explicitly, in upgrade().
# Without it create_table would emit CREATE TYPE a second time and fail.
NOTIFICATION_TYPE = postgresql.ENUM(
    "new_booking",
    "emergency_escalation",
    "urgent_message",
    "failed_call",
    "daily_summary",
    "weekly_report",
    "calendar_disconnected",
    name="notification_type",
    create_type=False,
)
NOTIFICATION_CHANNEL = postgresql.ENUM(
    "email", "sms", name="notification_channel", create_type=False
)
NOTIFICATION_STATUS = postgresql.ENUM(
    "pending",
    "sent",
    "delivered",
    "failed",
    "suppressed",
    name="notification_status",
    create_type=False,
)
CONSENT_STATUS = postgresql.ENUM(
    "unknown", "granted", "revoked", name="consent_status", create_type=False
)

#: tenant-owned tables get the same RLS treatment as the rest of the schema
_RLS_TABLES = (
    "notification_preferences",
    "notification_deliveries",
    "sms_consents",
    "email_suppressions",
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (NOTIFICATION_TYPE, NOTIFICATION_CHANNEL, NOTIFICATION_STATUS, CONSENT_STATUS):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "notification_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("notification_type", NOTIFICATION_TYPE, nullable=False),
        sa.Column("channel", NOTIFICATION_CHANNEL, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("destination", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "notification_type", "channel", name="uq_notification_pref"
        ),
    )
    op.create_index(
        "ix_notification_preferences_tenant_id", "notification_preferences", ["tenant_id"]
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calls.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notification_type", NOTIFICATION_TYPE, nullable=False),
        sa.Column("channel", NOTIFICATION_CHANNEL, nullable=False),
        sa.Column("template", sa.String(80), nullable=False),
        sa.Column("recipient_masked", sa.String(120), nullable=False),
        sa.Column(
            "status", NOTIFICATION_STATUS, nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("provider_message_id", sa.String(120), nullable=True),
        sa.Column("provider_response", postgresql.JSONB(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failure_category", sa.String(80), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )
    op.create_index(
        "ix_notification_deliveries_tenant_created",
        "notification_deliveries",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_notification_deliveries_provider_id",
        "notification_deliveries",
        ["provider_message_id"],
    )

    op.create_table(
        "sms_consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone_hash", sa.String(64), nullable=False),
        sa.Column("phone_last_four", sa.String(4), nullable=True),
        sa.Column("country", sa.String(2), nullable=False, server_default=sa.text("'US'")),
        sa.Column("status", CONSENT_STATUS, nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("source", sa.String(80), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "phone_hash", name="uq_sms_consent_tenant_phone"),
    )
    op.create_index("ix_sms_consents_tenant_id", "sms_consents", ["tenant_id"])

    op.create_table(
        "email_suppressions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column(
            "reason", sa.String(40), nullable=False, server_default=sa.text("'unsubscribed'")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "email_hash", name="uq_email_suppression"),
    )
    op.create_index("ix_email_suppressions_tenant_id", "email_suppressions", ["tenant_id"])

    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_table("email_suppressions")
    op.drop_table("sms_consents")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_preferences")
    bind = op.get_bind()
    for enum in (CONSENT_STATUS, NOTIFICATION_STATUS, NOTIFICATION_CHANNEL, NOTIFICATION_TYPE):
        enum.drop(bind, checkfirst=True)
