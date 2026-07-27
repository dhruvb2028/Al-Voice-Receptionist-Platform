"""Post-call processing pipeline.

One job per completed call, delivered by QStash (at-least-once).
Idempotent by call: a processed call acknowledges immediately; QStash's
bounded retries plus the dead-letter status cover persistent failures.

Nothing here overwrites authoritative call-time facts: a committed
booking's status is never changed by extraction, and the extraction's
outcome only fills a missing outcome.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from ai_database.audit import write_audit
from ai_database.enums import (
    CallOutcome,
    DeliveryStatus,
    ProcessingStatus,
    TranscriptStatus,
    TurnRole,
)
from ai_database.models import Booking, Call, Message, Turn, UsageRecord
from ai_providers.errors import ProviderError
from ai_providers.llm import ChatMessage, LLMProvider, LLMUsage
from ai_providers.messaging import EmailProvider
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

MAX_TRANSCRIPT_CHARS = 24_000


class CallExtraction(BaseModel):
    """Structured facts extracted from the transcript."""

    intent: str = Field(max_length=120)
    service: str | None = Field(default=None, max_length=160)
    urgency: Literal["emergency", "urgent", "routine", "unknown"] = "unknown"
    outcome: Literal[
        "booked", "message_taken", "transferred", "answered_inquiry", "caller_hangup", "failed"
    ]
    customer_name: str | None = Field(default=None, max_length=200)
    phone_available: bool = False
    address_available: bool = False
    booking_status: Literal["confirmed", "attempted", "none"] = "none"
    scheduled_time: str | None = None
    message_status: Literal["taken", "none"] = "none"
    escalation_status: Literal["transferred", "attempted", "none"] = "none"
    unresolved_questions: list[str] = Field(default_factory=list, max_length=10)
    sentiment: Literal["positive", "neutral", "frustrated", "unknown"] = "unknown"
    follow_up_required: bool = False
    summary: str = Field(max_length=800)


def assemble_transcript(turns: list[Turn]) -> str:
    lines = []
    for turn in sorted(turns, key=lambda t: t.turn_index):
        if turn.role is TurnRole.SYSTEM or not turn.text:
            continue
        speaker = "Caller" if turn.role is TurnRole.CALLER else "Receptionist"
        lines.append(f"{speaker}: {turn.text}")
    transcript = "\n".join(lines)
    return transcript[-MAX_TRANSCRIPT_CHARS:]


_EXTRACTION_PROMPT = (
    "You review phone-call transcripts for a home-services receptionist "
    "platform. Produce ONLY a JSON object matching this schema (no prose): "
    "intent, service, urgency (emergency|urgent|routine|unknown), outcome "
    "(booked|message_taken|transferred|answered_inquiry|caller_hangup|failed), "
    "customer_name, phone_available (bool), address_available (bool), "
    "booking_status (confirmed|attempted|none), scheduled_time, message_status "
    "(taken|none), escalation_status (transferred|attempted|none), "
    "unresolved_questions (list), sentiment (positive|neutral|frustrated|unknown), "
    "follow_up_required (bool), summary (2-3 sentences, plain language). "
    "Facts only from the transcript — never invent details."
)


async def extract_structured(
    llm: LLMProvider, transcript: str
) -> tuple[CallExtraction | None, LLMUsage]:
    """LLM extraction with strict validation; malformed output returns
    None rather than corrupting the record. Usage is reported even on
    failure so token spend is always accounted for."""
    try:
        stream = await llm.stream(
            messages=[
                ChatMessage(role="system", content=_EXTRACTION_PROMPT),
                ChatMessage(role="user", content=transcript or "(empty call)"),
            ],
            max_tokens=600,
        )
        parts = [d.text async for d in stream.deltas() if d.kind == "text" and d.text]
        usage = (await stream.result()).usage
    except ProviderError as exc:
        logger.warning("extraction_llm_failed", error=exc.category)
        return None, LLMUsage()

    raw = "".join(parts).strip()
    # Models sometimes wrap JSON in fences; strip defensively.
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    try:
        return CallExtraction.model_validate(json.loads(raw)), usage
    except (ValueError, ValidationError) as exc:
        logger.warning("extraction_malformed", error=str(exc)[:200])
        return None, usage


async def process_call(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    llm: LLMProvider,
    email: EmailProvider | None = None,
    notify_to: str | None = None,
) -> ProcessingStatus:
    """Run the full post-call pipeline for one call. Idempotent."""
    call = (await session.execute(select(Call).where(Call.id == call_id))).scalar_one_or_none()
    if call is None:
        raise LookupError("call not found")

    # Duplicate-delivery protection: complete jobs acknowledge silently.
    if call.post_processing_status is ProcessingStatus.COMPLETE:
        return ProcessingStatus.COMPLETE

    call.post_processing_status = ProcessingStatus.IN_PROGRESS
    await session.flush()

    turns = (
        (
            await session.execute(
                select(Turn).where(Turn.call_id == call_id, Turn.tenant_id == call.tenant_id)
            )
        )
        .scalars()
        .all()
    )
    transcript = assemble_transcript(list(turns))

    extraction, usage = await extract_structured(llm, transcript)
    tokens = usage.prompt_tokens + usage.completion_tokens
    if tokens:
        session.add(
            UsageRecord(
                tenant_id=call.tenant_id,
                call_id=call_id,
                provider="groq",
                usage_type="llm_postcall_tokens",
                quantity=tokens,
                unit="tokens",
            )
        )

    if extraction is None:
        # Bounded retries happen at the QStash layer; after exhaustion the
        # delivery dead-letters and this status flags the call for review.
        call.post_processing_status = ProcessingStatus.FAILED
        await write_audit(
            session,
            action="post_call.extraction_failed",
            actor_external_user_id=None,
            actor_role="worker",
            tenant_id=call.tenant_id,
            resource_type="call",
            resource_id=str(call_id),
        )
        await session.flush()
        return ProcessingStatus.FAILED

    # Authoritative facts win: a committed booking is never downgraded by
    # the model's reading of the transcript.
    booking = (
        await session.execute(
            select(Booking).where(Booking.call_id == call_id, Booking.tenant_id == call.tenant_id)
        )
    ).scalar_one_or_none()
    if booking is not None and booking.status.value == "confirmed":
        extraction.booking_status = "confirmed"
        extraction.outcome = "booked"

    if call.outcome is None:
        call.outcome = CallOutcome(extraction.outcome)
    if call.urgency is None and extraction.urgency != "unknown":
        from ai_database.enums import Urgency

        call.urgency = Urgency(extraction.urgency)

    call.transcript_status = TranscriptStatus.COMPLETE

    # Notifications (deduplicated by call-scoped idempotency key; a
    # duplicate delivery finds the key used and the original stands).
    if email is not None and notify_to:
        template = None
        variables: dict[str, str] = {"summary": extraction.summary}
        if extraction.outcome == "booked":
            template = "booking_confirmation"
            variables["time"] = extraction.scheduled_time or "see calendar"
        elif extraction.message_status == "taken":
            template = "urgent_escalation" if extraction.urgency == "emergency" else "new_message"
        if template:
            try:
                await email.send_template(
                    to_email=notify_to,
                    template=template,
                    variables=variables,
                    idempotency_key=f"postcall:{call_id}:{template}",
                )
            except ProviderError as exc:
                if exc.category != "duplicate_send":
                    logger.warning("post_call_notification_failed", error=exc.category)
            else:
                await _mark_messages_delivered(session, call)

    # Evaluation metadata rides on the audit trail for the eval harness.
    await write_audit(
        session,
        action="post_call.processed",
        actor_external_user_id=None,
        actor_role="worker",
        tenant_id=call.tenant_id,
        resource_type="call",
        resource_id=str(call_id),
        after={
            "outcome": extraction.outcome,
            "sentiment": extraction.sentiment,
            "follow_up_required": extraction.follow_up_required,
            "unresolved_questions": len(extraction.unresolved_questions),
            "summary": extraction.summary,
        },
    )

    call.post_processing_status = ProcessingStatus.COMPLETE
    await session.flush()
    logger.info("post_call_complete", call_id=str(call_id), outcome=extraction.outcome)
    return ProcessingStatus.COMPLETE


async def _mark_messages_delivered(session: AsyncSession, call: Call) -> None:
    pending = (
        (
            await session.execute(
                select(Message).where(
                    Message.call_id == call.id,
                    Message.tenant_id == call.tenant_id,
                    Message.delivery_status == DeliveryStatus.PENDING,
                )
            )
        )
        .scalars()
        .all()
    )
    for message in pending:
        message.delivery_status = DeliveryStatus.SENT
        message.delivered_at = datetime.now(UTC)
