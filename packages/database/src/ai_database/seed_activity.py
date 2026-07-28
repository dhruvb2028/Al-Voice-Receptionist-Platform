"""Seed realistic call activity for the demo tenant.

The configuration seed produces a tenant that can answer a call but has
no history, so every dashboard page renders its empty state. This adds
the calls, turns, bookings, messages, escalations, usage, and post-call
summaries that make the dashboard show the product rather than a set of
"nothing here yet" cards.

Idempotent: keyed on the provider call SIDs it creates, so running it
twice adds nothing.

Run:  uv run python -m ai_database.seed_activity
Env:  DATABASE_DIRECT_URL (or DATABASE_URL)

Everything here is invented demonstration data for a fictional business.
It is never used to compute a metric shown to a real client.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_database.audit import write_audit
from ai_database.engine import create_engine, create_session_factory
from ai_database.enums import (
    BookingStatus,
    CallDirection,
    CallOutcome,
    CallTransport,
    DeliveryStatus,
    EscalationReason,
    EscalationStatus,
    GuardrailAction,
    GuardrailType,
    ProcessingStatus,
    ReconciliationStatus,
    RecordingStatus,
    ToolExecutionStatus,
    TranscriptStatus,
    TurnRole,
    Urgency,
)
from ai_database.models import (
    Booking,
    Call,
    Escalation,
    GuardrailEvent,
    Message,
    Service,
    Tenant,
    ToolExecution,
    Turn,
    UsageRecord,
)
from ai_database.seed import DEMO_SLUG

logger = structlog.get_logger()

SID_PREFIX = "CAdemo"


def _sid(name: str) -> str:
    return f"{SID_PREFIX}_{name}"


async def seed_demo_activity(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Populate the demo tenant's call history. Returns calls created."""
    now = now or datetime.now(UTC)

    tenant = (
        await session.execute(select(Tenant).where(Tenant.slug == DEMO_SLUG))
    ).scalar_one_or_none()
    if tenant is None:
        raise SystemExit("Run `python -m ai_database.seed` first — no demo tenant found.")

    existing = (
        await session.execute(
            select(Call.id).where(Call.provider_call_sid.like(f"{SID_PREFIX}%")).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.info("activity_seed_skipped", reason="already seeded")
        return 0

    services = {
        s.name: s
        for s in (
            await session.execute(select(Service).where(Service.tenant_id == tenant.id))
        ).scalars()
    }
    drain = next((s for s in services.values() if "drain" in s.name.lower()), None)

    created = 0

    # --- 1. A clean booking -------------------------------------------------
    booked_at = now - timedelta(days=1, hours=3)
    booked = Call(
        tenant_id=tenant.id,
        provider_call_sid=_sid("booked"),
        from_number_last_four="4821",
        to_number="+16175550142",
        started_at=booked_at,
        answered_at=booked_at + timedelta(seconds=1),
        ended_at=booked_at + timedelta(seconds=214),
        duration_seconds=214,
        direction=CallDirection.INBOUND,
        transport=CallTransport.PHONE,
        outcome=CallOutcome.BOOKED,
        urgency=Urgency.ROUTINE,
        recording_status=RecordingStatus.STORED,
        recording_object_key=f"tenants/{tenant.id}/calls/demo-booked/recording.wav",
        transcript_status=TranscriptStatus.COMPLETE,
        post_processing_status=ProcessingStatus.COMPLETE,
        estimated_cost_cents=31,
    )
    session.add(booked)
    await session.flush()
    created += 1

    _add_turns(
        session,
        tenant.id,
        booked,
        booked_at,
        [
            ("assistant", "Thanks for calling Harbor Plumbing! How can I help you today?", 640),
            ("caller", "Hi, my kitchen sink is draining really slowly.", None),
            ("assistant", "I can help with that. When would suit you for a visit?", 820),
            ("caller", "Tomorrow morning if you have anything.", None),
            ("assistant", "Let me check tomorrow morning for you.", 910),
            ("caller", "Thanks.", None),
            ("assistant", "I have ten o'clock free. Shall I book that in?", 780),
            ("caller", "Yes please, that works. It's Dana Whitfield.", None),
            ("assistant", "Booked for ten tomorrow, Dana. You'll get a confirmation.", 700),
        ],
    )
    session.add(
        ToolExecution(
            tenant_id=tenant.id,
            call_id=booked.id,
            tool_name="check_availability",
            status=ToolExecutionStatus.SUCCESS,
            started_at=booked_at + timedelta(seconds=96),
            completed_at=booked_at + timedelta(seconds=97),
            duration_ms=412,
            input_redacted={"service": "Drain cleaning", "date": "tomorrow"},
            result_redacted={"slots": ["10:00", "13:30"]},
        )
    )
    session.add(
        ToolExecution(
            tenant_id=tenant.id,
            call_id=booked.id,
            tool_name="book_appointment",
            status=ToolExecutionStatus.SUCCESS,
            started_at=booked_at + timedelta(seconds=182),
            completed_at=booked_at + timedelta(seconds=184),
            duration_ms=1_240,
            input_redacted={"service": "Drain cleaning", "slot": "10:00"},
            result_redacted={"booked": True},
        )
    )
    session.add(
        Booking(
            tenant_id=tenant.id,
            call_id=booked.id,
            service_id=drain.id if drain else None,
            customer_name="Dana Whitfield",
            customer_phone_last_four="4821",
            scheduled_at=now + timedelta(days=1, hours=2),
            timezone="America/New_York",
            idempotency_key=f"demo-booking-{uuid.uuid4().hex[:10]}",
            status=BookingStatus.CONFIRMED,
            external_calendar_event_id="demo_gcal_event_1",
            reconciliation_status=ReconciliationStatus.NOT_REQUIRED,
            created_at=booked_at + timedelta(seconds=184),
            updated_at=booked_at + timedelta(seconds=184),
        )
    )
    await _summary(
        session,
        tenant.id,
        booked.id,
        outcome="booked",
        sentiment="positive",
        follow_up=False,
        summary=(
            "Caller reported a slow-draining kitchen sink and booked a drain "
            "cleaning for tomorrow at 10:00."
        ),
    )

    # --- 2. An emergency, escalated ----------------------------------------
    emergency_at = now - timedelta(hours=20)
    emergency = Call(
        tenant_id=tenant.id,
        provider_call_sid=_sid("emergency"),
        from_number_last_four="9014",
        to_number="+16175550142",
        started_at=emergency_at,
        answered_at=emergency_at + timedelta(seconds=1),
        ended_at=emergency_at + timedelta(seconds=41),
        duration_seconds=41,
        direction=CallDirection.INBOUND,
        transport=CallTransport.PHONE,
        outcome=CallOutcome.TRANSFERRED,
        urgency=Urgency.EMERGENCY,
        recording_status=RecordingStatus.STORED,
        recording_object_key=f"tenants/{tenant.id}/calls/demo-emergency/recording.wav",
        transcript_status=TranscriptStatus.COMPLETE,
        post_processing_status=ProcessingStatus.COMPLETE,
        estimated_cost_cents=9,
    )
    session.add(emergency)
    await session.flush()
    created += 1

    _add_turns(
        session,
        tenant.id,
        emergency,
        emergency_at,
        [
            ("assistant", "Thanks for calling Harbor Plumbing! How can I help you today?", 610),
            ("caller", "There's water pouring through my kitchen ceiling.", None),
            (
                "assistant",
                "That sounds like an emergency - I'm connecting you to someone right now. "
                "Please stay on the line.",
                520,
            ),
        ],
    )
    session.add(
        GuardrailEvent(
            tenant_id=tenant.id,
            call_id=emergency.id,
            guardrail_type=GuardrailType.EMERGENCY,
            action=GuardrailAction.ESCALATED,
            input_redacted={"trigger": "burst pipe"},
            created_at=emergency_at + timedelta(seconds=18),
        )
    )
    session.add(
        ToolExecution(
            tenant_id=tenant.id,
            call_id=emergency.id,
            tool_name="transfer_to_human",
            status=ToolExecutionStatus.SUCCESS,
            started_at=emergency_at + timedelta(seconds=19),
            completed_at=emergency_at + timedelta(seconds=21),
            duration_ms=1_900,
            input_redacted={"reason": "emergency"},
            result_redacted={"connected": True},
        )
    )
    session.add(
        Escalation(
            tenant_id=tenant.id,
            call_id=emergency.id,
            reason=EscalationReason.EMERGENCY,
            destination_last_four="0100",
            initiated_at=emergency_at + timedelta(seconds=19),
            connected_at=emergency_at + timedelta(seconds=27),
            ended_at=emergency_at + timedelta(seconds=41),
            status=EscalationStatus.CONNECTED,
        )
    )
    await _summary(
        session,
        tenant.id,
        emergency.id,
        outcome="transferred",
        sentiment="frustrated",
        follow_up=True,
        summary=(
            "Water coming through a ceiling. Transferred to the on-call engineer within 20 seconds."
        ),
    )

    # --- 3. A message taken after hours ------------------------------------
    message_at = now - timedelta(days=2, hours=13)
    messaged = Call(
        tenant_id=tenant.id,
        provider_call_sid=_sid("message"),
        from_number_last_four="7732",
        to_number="+16175550142",
        started_at=message_at,
        answered_at=message_at + timedelta(seconds=1),
        ended_at=message_at + timedelta(seconds=96),
        duration_seconds=96,
        direction=CallDirection.INBOUND,
        transport=CallTransport.PHONE,
        outcome=CallOutcome.MESSAGE_TAKEN,
        urgency=Urgency.ROUTINE,
        recording_status=RecordingStatus.STORED,
        recording_object_key=f"tenants/{tenant.id}/calls/demo-message/recording.wav",
        transcript_status=TranscriptStatus.COMPLETE,
        post_processing_status=ProcessingStatus.COMPLETE,
        estimated_cost_cents=17,
    )
    session.add(messaged)
    await session.flush()
    created += 1

    _add_turns(
        session,
        tenant.id,
        messaged,
        message_at,
        [
            ("assistant", "Thanks for calling Harbor Plumbing! How can I help you today?", 600),
            ("caller", "How much would it be to replace a water heater?", None),
            (
                "assistant",
                "The team will confirm the exact price after reviewing the job. "
                "Can I take a message so someone can call you back?",
                760,
            ),
            ("caller", "Yes, it's Marcus Bell, best on this number.", None),
            ("assistant", "Thanks Marcus, I've noted that down. They'll be in touch.", 690),
        ],
    )
    # The receptionist tried to quote and was stopped. Visible in the
    # dashboard as a guardrail intervention on the call detail.
    session.add(
        GuardrailEvent(
            tenant_id=tenant.id,
            call_id=messaged.id,
            guardrail_type=GuardrailType.PRICE_INVENTION,
            action=GuardrailAction.REWRITTEN,
            input_redacted={"blocked_amounts_cents": [95000]},
            created_at=message_at + timedelta(seconds=34),
        )
    )
    session.add(
        ToolExecution(
            tenant_id=tenant.id,
            call_id=messaged.id,
            tool_name="take_message",
            status=ToolExecutionStatus.SUCCESS,
            started_at=message_at + timedelta(seconds=74),
            completed_at=message_at + timedelta(seconds=75),
            duration_ms=380,
            input_redacted={"urgency": "routine"},
            result_redacted={"stored": True},
        )
    )
    session.add(
        Message(
            tenant_id=tenant.id,
            call_id=messaged.id,
            customer_name="Marcus Bell",
            customer_phone_last_four="7732",
            body_encrypted="v1:demo-placeholder-ciphertext",
            urgency=Urgency.ROUTINE,
            delivery_status=DeliveryStatus.SENT,
            delivered_at=message_at + timedelta(seconds=120),
            created_at=message_at + timedelta(seconds=75),
        )
    )
    await _summary(
        session,
        tenant.id,
        messaged.id,
        outcome="message_taken",
        sentiment="neutral",
        follow_up=True,
        summary=(
            "Asked for a water heater replacement price. No approved price exists, "
            "so a message was taken for a callback."
        ),
    )

    # --- 4. A failed call ---------------------------------------------------
    failed_at = now - timedelta(hours=6)
    failed = Call(
        tenant_id=tenant.id,
        provider_call_sid=_sid("failed"),
        from_number_last_four="2286",
        to_number="+16175550142",
        started_at=failed_at,
        answered_at=failed_at + timedelta(seconds=1),
        ended_at=failed_at + timedelta(seconds=12),
        duration_seconds=12,
        direction=CallDirection.INBOUND,
        transport=CallTransport.PHONE,
        outcome=CallOutcome.CALLER_HANGUP,
        recording_status=RecordingStatus.DISABLED,
        transcript_status=TranscriptStatus.PARTIAL,
        post_processing_status=ProcessingStatus.COMPLETE,
        estimated_cost_cents=3,
        failure_category=None,
    )
    session.add(failed)
    await session.flush()
    created += 1
    _add_turns(
        session,
        tenant.id,
        failed,
        failed_at,
        [("assistant", "Thanks for calling Harbor Plumbing! How can I help you today?", 630)],
    )

    # --- usage --------------------------------------------------------------
    for call, minutes in ((booked, 4), (emergency, 1), (messaged, 2), (failed, 1)):
        session.add(
            UsageRecord(
                tenant_id=tenant.id,
                call_id=call.id,
                provider="twilio",
                usage_type="call_minutes",
                quantity=minutes,
                unit="minutes",
                cost_cents=minutes * 2,
                recorded_at=call.started_at,
            )
        )

    await session.flush()
    logger.info("activity_seed_created", calls=created)
    return created


def _add_turns(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    call: Call,
    started: datetime,
    script: list[tuple[str, str, int | None]],
) -> None:
    offset = 0
    for index, (role, text, latency) in enumerate(script):
        offset += 8 + index * 3
        session.add(
            Turn(
                tenant_id=tenant_id,
                call_id=call.id,
                turn_index=index,
                role=TurnRole.ASSISTANT if role == "assistant" else TurnRole.CALLER,
                text=text,
                started_at=started + timedelta(seconds=offset),
                ended_at=started + timedelta(seconds=offset + 4),
                total_latency_ms=latency,
                llm_ttft_ms=int(latency * 0.35) if latency else None,
                tts_ttfb_ms=int(latency * 0.25) if latency else None,
                created_at=started + timedelta(seconds=offset),
            )
        )


async def _summary(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    *,
    outcome: str,
    sentiment: str,
    follow_up: bool,
    summary: str,
) -> None:
    """The post-call worker writes its extraction to the audit trail; the
    dashboard reads the summary from there."""
    await write_audit(
        session,
        action="post_call.processed",
        actor_external_user_id=None,
        actor_role="worker",
        tenant_id=tenant_id,
        resource_type="call",
        resource_id=str(call_id),
        after={
            "outcome": outcome,
            "sentiment": sentiment,
            "follow_up_required": follow_up,
            "unresolved_questions": 0,
            "summary": summary,
        },
    )


async def main() -> None:
    url = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_DIRECT_URL (or DATABASE_URL) to seed.")
    engine = create_engine(url)
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        count = await seed_demo_activity(session)
    await engine.dispose()
    print(f"Seeded {count} demo call(s).")


if __name__ == "__main__":
    asyncio.run(main())
