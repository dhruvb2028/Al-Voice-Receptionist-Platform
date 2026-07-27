"""Call-time persistence and telemetry.

One recorder per live call. Events are logged structurally the moment
they happen (never blocking audio); turns, tool executions, and the
final call summary persist to the database off the hot path. Metrics
follow the PRD's latency breakdown so p50/p95 dashboards slice by
stage, tenant, and call.

Log redaction is centralized in ai_telemetry (phone numbers, addresses,
tokens, transcript bodies) — this module tags events and durations, and
deliberately never logs transcript text.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger()


class CallEvent(StrEnum):
    CALL_STARTED = "call_started"
    GREETING_PLAYED = "greeting_played"
    CALLER_SPEECH_BEGAN = "caller_speech_began"
    PARTIAL_TRANSCRIPT = "partial_transcript"
    FINAL_TRANSCRIPT = "final_transcript"
    CALLER_TURN_ENDED = "caller_turn_ended"
    AGENT_GENERATION_BEGAN = "agent_generation_began"
    AGENT_FIRST_TOKEN = "agent_first_token"  # noqa: S105 — event name, not a secret
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TTS_BEGAN = "tts_began"
    FIRST_AUDIO_PLAYED = "first_audio_played"
    AGENT_INTERRUPTED = "agent_interrupted"
    TRANSFER_INITIATED = "transfer_initiated"
    TRANSFER_CONNECTED = "transfer_connected"
    MESSAGE_CREATED = "message_created"
    CALL_ENDED = "call_ended"
    CALL_FAILED = "call_failed"


@dataclass
class TurnMetrics:
    """Latency breakdown for one turn (milliseconds)."""

    turn_index: int
    vad_start_ms: int | None = None
    endpointing_ms: int | None = None
    stt_finalization_ms: int | None = None
    llm_first_token_ms: int | None = None
    tool_duration_ms: int | None = None
    tts_first_byte_ms: int | None = None
    first_playback_ms: int | None = None
    total_latency_ms: int | None = None
    barge_in_stop_ms: int | None = None
    interrupted: bool = False


@dataclass
class CallSummaryMetrics:
    duration_seconds: int | None = None
    turn_count: int = 0
    outcome: str | None = None
    booked: bool = False
    message_taken: bool = False
    escalated: bool = False
    #: resolved without human transfer (excludes mandatory escalations)
    contained: bool | None = None
    provider_usage: dict[str, int] = field(default_factory=dict)
    estimated_cost_cents: int | None = None
    error_category: str | None = None


# Rough per-unit provider rates (cents) for live cost estimation; exact
# reconciliation happens against provider invoices monthly.
_COST_RATES = {
    "stt_seconds": 0.0722,  # Deepgram nova streaming / sec
    "llm_tokens": 0.000079,  # Groq blended per token
    "tts_characters": 0.0030,  # Cartesia per character
    "telephony_seconds": 0.0140,  # Twilio inbound per second
}


def estimate_cost_cents(usage: dict[str, int]) -> int:
    return int(sum(_COST_RATES.get(kind, 0.0) * amount for kind, amount in usage.items()))


class CallRecorder:
    """Persists events, turns, and the final summary for one call."""

    def __init__(
        self,
        *,
        tenant_id: str,
        call_id: str,
        session_factory: Any | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.call_id = call_id
        self._session_factory = session_factory
        self.events: list[tuple[CallEvent, datetime, dict[str, Any]]] = []
        self.turns: list[TurnMetrics] = []
        self.summary = CallSummaryMetrics()
        self._started_at = datetime.now(UTC)
        self._write_tasks: set[asyncio.Task[None]] = set()

    # -- events --------------------------------------------------------------

    def record_event(
        self,
        event: CallEvent,
        *,
        turn_index: int | None = None,
        duration_ms: int | None = None,
        provider_request_id: str | None = None,
        error_category: str | None = None,
        **extra: Any,
    ) -> None:
        """Log + buffer an event. Never blocks; never contains PII."""
        now = datetime.now(UTC)
        payload: dict[str, Any] = {k: v for k, v in extra.items() if v is not None}
        if error_category:
            payload["error_category"] = error_category
        self.events.append((event, now, payload))
        logger.info(
            event.value,
            call_id=self.call_id,
            tenant_id=self.tenant_id,
            turn_index=turn_index,
            duration_ms=duration_ms,
            provider_request_id=provider_request_id,
            error_category=error_category,
        )

        if event is CallEvent.AGENT_INTERRUPTED and turn_index is not None:
            metrics = self._turn(turn_index)
            metrics.interrupted = True
            if duration_ms is not None:
                metrics.barge_in_stop_ms = duration_ms
        elif event is CallEvent.TRANSFER_INITIATED:
            self.summary.escalated = True
        elif event is CallEvent.MESSAGE_CREATED:
            self.summary.message_taken = True
        elif event is CallEvent.CALL_FAILED:
            self.summary.error_category = error_category or "unknown"

    # -- turn metrics --------------------------------------------------------

    def _turn(self, turn_index: int) -> TurnMetrics:
        for metrics in self.turns:
            if metrics.turn_index == turn_index:
                return metrics
        metrics = TurnMetrics(turn_index=turn_index)
        self.turns.append(metrics)
        return metrics

    def record_turn_metrics(self, turn_index: int, **values: int | None) -> TurnMetrics:
        metrics = self._turn(turn_index)
        for key, value in values.items():
            if value is not None and hasattr(metrics, key):
                setattr(metrics, key, value)
        return metrics

    def add_usage(self, kind: str, amount: int) -> None:
        self.summary.provider_usage[kind] = self.summary.provider_usage.get(kind, 0) + amount

    # -- persistence ---------------------------------------------------------

    def persist_turn(
        self,
        *,
        turn_index: int,
        role: str,
        text: str,
        metrics: TurnMetrics | None = None,
    ) -> None:
        """Schedule a turn write off the audio path."""
        if self._session_factory is None:
            return
        task = asyncio.create_task(
            self._write_turn(turn_index=turn_index, role=role, text=text, metrics=metrics)
        )
        self._write_tasks.add(task)
        task.add_done_callback(self._write_tasks.discard)

    async def _write_turn(
        self, *, turn_index: int, role: str, text: str, metrics: TurnMetrics | None
    ) -> None:
        from ai_database.enums import TurnRole
        from ai_database.models import Turn

        assert self._session_factory is not None  # guarded by persist_turn
        try:
            async with self._session_factory() as session, session.begin():
                session.add(
                    Turn(
                        tenant_id=uuid.UUID(self.tenant_id),
                        call_id=uuid.UUID(self.call_id),
                        turn_index=turn_index,
                        role=TurnRole(role),
                        text=text,
                        started_at=datetime.now(UTC),
                        endpointing_ms=metrics.endpointing_ms if metrics else None,
                        stt_finalization_ms=(metrics.stt_finalization_ms if metrics else None),
                        llm_ttft_ms=metrics.llm_first_token_ms if metrics else None,
                        tts_ttfb_ms=metrics.tts_first_byte_ms if metrics else None,
                        first_playback_ms=metrics.first_playback_ms if metrics else None,
                        total_latency_ms=metrics.total_latency_ms if metrics else None,
                        barge_in=bool(metrics and metrics.barge_in_stop_ms is not None),
                        interrupted=bool(metrics and metrics.interrupted),
                    )
                )
        except Exception:
            logger.exception("turn_persist_failed", call_id=self.call_id)

    async def finalize(
        self,
        *,
        outcome: str,
        booked: bool = False,
    ) -> CallSummaryMetrics:
        """Write the call summary and usage rows; returns the metrics."""
        now = datetime.now(UTC)
        self.summary.duration_seconds = int((now - self._started_at).total_seconds())
        self.summary.turn_count = len(self.turns)
        self.summary.outcome = outcome
        self.summary.booked = booked
        # Containment: resolved without transfer; failed calls never count.
        self.summary.contained = (
            outcome in ("booked", "message_taken", "answered_inquiry")
            and not self.summary.escalated
        )
        self.add_usage("telephony_seconds", self.summary.duration_seconds)
        self.summary.estimated_cost_cents = estimate_cost_cents(self.summary.provider_usage)

        # Wait for stragglers so nothing is lost at hangup.
        if self._write_tasks:
            await asyncio.gather(*self._write_tasks, return_exceptions=True)

        if self._session_factory is not None:
            await self._write_summary()

        logger.info(
            CallEvent.CALL_ENDED.value,
            call_id=self.call_id,
            tenant_id=self.tenant_id,
            duration_seconds=self.summary.duration_seconds,
            turns=self.summary.turn_count,
            outcome=outcome,
            contained=self.summary.contained,
            estimated_cost_cents=self.summary.estimated_cost_cents,
        )
        return self.summary

    async def _write_summary(self) -> None:
        from ai_database.enums import CallOutcome
        from ai_database.models import Call, UsageRecord
        from sqlalchemy import select

        assert self._session_factory is not None  # guarded by finalize
        try:
            async with self._session_factory() as session, session.begin():
                call = (
                    await session.execute(select(Call).where(Call.id == uuid.UUID(self.call_id)))
                ).scalar_one_or_none()
                if call is not None:
                    call.ended_at = datetime.now(UTC)
                    call.duration_seconds = self.summary.duration_seconds
                    call.outcome = (
                        CallOutcome(self.summary.outcome) if self.summary.outcome else None
                    )
                    call.estimated_cost_cents = self.summary.estimated_cost_cents
                    call.failure_category = self.summary.error_category
                for kind, amount in self.summary.provider_usage.items():
                    session.add(
                        UsageRecord(
                            tenant_id=uuid.UUID(self.tenant_id),
                            call_id=uuid.UUID(self.call_id),
                            provider=kind.split("_")[0],
                            usage_type=kind,
                            quantity=amount,
                            unit=kind.rsplit("_", 1)[-1],
                            cost_cents=int(_COST_RATES.get(kind, 0.0) * amount),
                        )
                    )
        except Exception:
            logger.exception("summary_persist_failed", call_id=self.call_id)
