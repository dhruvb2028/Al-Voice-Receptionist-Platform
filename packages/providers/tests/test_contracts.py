"""Contract tests: every mock implements its interface's semantics.

These are the behavioral guarantees the real adapters must also honor —
when a vendor adapter lands, it runs against the same assertions.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from ai_providers.cache import CacheProvider, MockCacheProvider
from ai_providers.calendar import CalendarProvider, CalendarSlot, MockCalendarProvider
from ai_providers.errors import (
    CredentialRevokedError,
    DuplicateSendError,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ai_providers.llm import (
    ChatMessage,
    LLMProvider,
    LLMToolCall,
    MockLLMProvider,
    MockTurn,
    parse_tool_arguments,
)
from ai_providers.messaging import MockEmailProvider, MockSMSProvider
from ai_providers.retry import with_retries
from ai_providers.storage import MockStorageProvider, StorageProvider
from ai_providers.stt import MockSTTProvider, STTProvider
from ai_providers.telephony import MockTelephonyProvider, TelephonyProvider
from ai_providers.tts import MockTTSProvider, TTSProvider

NOW = datetime.now(UTC)


# --- Protocol conformance ----------------------------------------------------


def test_mocks_satisfy_protocols() -> None:
    assert isinstance(MockTelephonyProvider(), TelephonyProvider)
    assert isinstance(MockSTTProvider(), STTProvider)
    assert isinstance(MockLLMProvider(), LLMProvider)
    assert isinstance(MockTTSProvider(), TTSProvider)
    assert isinstance(MockCalendarProvider(), CalendarProvider)
    assert isinstance(MockStorageProvider(), StorageProvider)
    assert isinstance(MockCacheProvider(), CacheProvider)


# --- Telephony ---------------------------------------------------------------


def test_telephony_webhook_verification() -> None:
    provider = MockTelephonyProvider()
    params = {"CallSid": "CA1", "From": "+15550001111", "To": "+15550002222"}
    assert provider.verify_webhook(url="https://x", params=params, signature="valid-signature")
    assert not provider.verify_webhook(url="https://x", params=params, signature="forged")


async def test_telephony_clear_audio_flushes_pending() -> None:
    provider = MockTelephonyProvider()
    await provider.send_audio(call_sid="CA1", payload_b64="AAA")
    await provider.send_audio(call_sid="CA2", payload_b64="BBB")
    await provider.clear_audio(call_sid="CA1")
    assert ("CA1", "AAA") not in provider.sent_audio
    assert ("CA2", "BBB") in provider.sent_audio


def test_telephony_stream_twiml_contains_ws_url() -> None:
    provider = MockTelephonyProvider()
    twiml = provider.stream_response_twiml(
        ws_url="wss://voice.example/ws?token=t", announcement="This call may be recorded."
    )
    assert "wss://voice.example/ws?token=t" in twiml
    assert "This call may be recorded." in twiml


# --- STT ---------------------------------------------------------------------


async def test_stt_emits_partial_then_final() -> None:
    provider = MockSTTProvider(["hello I need a plumber"])
    session = await provider.connect(sample_rate=8000, encoding="mulaw", language="en")
    await session.send_audio(b"\x00" * 160)
    events = [event async for event in session.events()]
    assert len(events) == 2
    assert events[0].is_final is False
    assert events[1].is_final is True
    assert events[1].text == "hello I need a plumber"
    assert events[1].confidence and events[1].confidence > 0.9
    assert events[1].finalization_ms is not None


async def test_stt_disconnect_raises_transient_error() -> None:
    provider = MockSTTProvider(["hi"])
    session = await provider.connect(sample_rate=8000, encoding="mulaw", language="en")
    session.disconnect_after_chunks = 1
    await session.send_audio(b"\x00")
    with pytest.raises(ProviderUnavailableError) as excinfo:
        await session.send_audio(b"\x00")
    assert excinfo.value.transient is True


async def test_stt_closed_session_rejects_audio() -> None:
    provider = MockSTTProvider([])
    session = await provider.connect(sample_rate=8000, encoding="mulaw", language="en")
    await session.close()
    with pytest.raises(ProviderUnavailableError):
        await session.send_audio(b"\x00")


# --- LLM ---------------------------------------------------------------------


async def test_llm_streams_text_then_done_with_usage() -> None:
    provider = MockLLMProvider([MockTurn(text="I can help with that")])
    stream = await provider.stream(messages=[ChatMessage(role="user", content="hi")])
    deltas = [delta async for delta in stream.deltas()]
    assert deltas[-1].kind == "done"
    text = "".join(d.text or "" for d in deltas if d.kind == "text")
    assert text == "I can help with that"
    result = await stream.result()
    assert result.usage.completion_tokens > 0
    assert result.usage.first_token_ms is not None
    assert result.usage.provider_request_id


async def test_llm_emits_tool_calls() -> None:
    call = LLMToolCall(id="t1", name="check_availability", arguments={"service": "drain"})
    provider = MockLLMProvider([MockTurn(text="One moment", tool_calls=[call])])
    stream = await provider.stream(messages=[ChatMessage(role="user", content="book me")])
    deltas = [delta async for delta in stream.deltas()]
    tool_deltas = [d for d in deltas if d.kind == "tool_call"]
    assert len(tool_deltas) == 1
    assert tool_deltas[0].tool_call and tool_deltas[0].tool_call.name == "check_availability"
    result = await stream.result()
    assert result.tool_calls == [call]


async def test_llm_cancellation_stops_stream() -> None:
    provider = MockLLMProvider([MockTurn(text="a very long answer " * 10)])
    stream = await provider.stream(messages=[ChatMessage(role="user", content="hi")])
    iterator = stream.deltas()
    await anext(iterator)
    await stream.cancel()
    remaining = [delta async for delta in iterator]
    assert all(d.kind != "tool_call" for d in remaining)
    result = await stream.result()
    assert result.cancelled is True


def test_tool_argument_parsing_fails_closed() -> None:
    assert parse_tool_arguments('{"a": 1}') == {"a": 1}
    with pytest.raises(ProviderResponseError):
        parse_tool_arguments("not json")
    with pytest.raises(ProviderResponseError):
        parse_tool_arguments('["not", "an", "object"]')


# --- TTS ---------------------------------------------------------------------


async def test_tts_streams_chunks_with_first_byte_latency() -> None:
    provider = MockTTSProvider()
    session = await provider.open_session(voice_id="warm-1", sample_rate=8000, encoding="mulaw")
    await session.send_text("Hello there, how can I help?")
    chunks = [chunk async for chunk in session.chunks()]
    assert chunks
    assert chunks[0].first_byte_ms is not None
    assert all(chunk.first_byte_ms is None for chunk in chunks[1:])
    assert session.voice_id == "warm-1"


async def test_tts_cancel_drops_pending_audio() -> None:
    provider = MockTTSProvider()
    session = await provider.open_session(voice_id="v", sample_rate=8000, encoding="mulaw")
    await session.send_text("This sentence will be interrupted before playback finishes")
    await session.cancel()
    chunks = [chunk async for chunk in session.chunks()]
    assert chunks == []


# --- Calendar ----------------------------------------------------------------


def _slot(start_hours: int, length_hours: int = 1) -> CalendarSlot:
    start = NOW + timedelta(hours=start_hours)
    return CalendarSlot(start=start, end=start + timedelta(hours=length_hours))


async def test_calendar_availability_and_booking_roundtrip() -> None:
    provider = MockCalendarProvider(free_slots=[_slot(24), _slot(48)])
    slots = await provider.check_availability(
        window_start=NOW, window_end=NOW + timedelta(days=3), duration_minutes=60
    )
    assert len(slots) == 2

    event = await provider.create_event(
        start=slots[0].start, end=slots[0].end, summary="Drain cleaning", description=""
    )
    fetched = await provider.fetch_event(event_id=event.event_id)
    assert fetched is not None and fetched.summary == "Drain cleaning"

    # The booked slot is no longer offered.
    slots_after = await provider.check_availability(
        window_start=NOW, window_end=NOW + timedelta(days=3), duration_minutes=60
    )
    assert len(slots_after) == 1

    await provider.cancel_event(event_id=event.event_id)
    cancelled = await provider.fetch_event(event_id=event.event_id)
    assert cancelled is not None and cancelled.cancelled is True


async def test_calendar_revalidate_slot_blocks_race() -> None:
    provider = MockCalendarProvider(free_slots=[_slot(24)])
    slot = provider.free_slots[0]
    assert await provider.revalidate_slot(start=slot.start, end=slot.end) is True
    await provider.create_event(start=slot.start, end=slot.end, summary="First", description="")
    # Second caller: the same slot no longer revalidates.
    assert await provider.revalidate_slot(start=slot.start, end=slot.end) is False
    with pytest.raises(ProviderResponseError):
        await provider.create_event(
            start=slot.start, end=slot.end, summary="Second", description=""
        )


async def test_calendar_detects_credential_revocation() -> None:
    provider = MockCalendarProvider(free_slots=[_slot(24)])
    provider.revoked = True
    with pytest.raises(CredentialRevokedError) as excinfo:
        await provider.validate_connection()
    assert excinfo.value.transient is False


# --- SMS / Email -------------------------------------------------------------


async def test_sms_duplicate_send_prevented() -> None:
    provider = MockSMSProvider()
    result = await provider.send_template(
        to_e164="+15550001111",
        template="booking_confirmation",
        variables={"time": "3 PM"},
        idempotency_key="call1-booking",
    )
    assert result.accepted
    with pytest.raises(DuplicateSendError):
        await provider.send_template(
            to_e164="+15550001111",
            template="booking_confirmation",
            variables={"time": "3 PM"},
            idempotency_key="call1-booking",
        )
    assert await provider.delivery_status(provider_message_id=result.provider_message_id) == (
        "delivered"
    )


async def test_unapproved_template_rejected() -> None:
    provider = MockEmailProvider()
    with pytest.raises(ProviderResponseError):
        await provider.send_template(
            to_email="a@example.com",
            template="marketing_blast",
            variables={},
            idempotency_key="k1",
        )


# --- Storage -----------------------------------------------------------------


async def test_storage_upload_sign_delete_with_retention() -> None:
    provider = MockStorageProvider()
    obj = await provider.upload(
        key="tenants/t1/calls/c1/recording.wav",
        data=b"RIFF....",
        content_type="audio/wav",
        retain_days=90,
    )
    assert obj.retain_until is not None

    signed = await provider.signed_url(key=obj.key, expires_seconds=900)
    assert signed.expires_at <= datetime.now(UTC) + timedelta(seconds=901)
    assert obj.key in signed.url

    assert await provider.delete(key=obj.key) is True
    assert await provider.delete(key=obj.key) is False  # idempotent
    assert await provider.head(key=obj.key) is None


async def test_storage_signing_missing_object_fails() -> None:
    provider = MockStorageProvider()
    with pytest.raises(ProviderResponseError):
        await provider.signed_url(key="does/not/exist")


# --- Cache -------------------------------------------------------------------


async def test_cache_tenant_config_roundtrip_is_tenant_scoped() -> None:
    provider = MockCacheProvider()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await provider.set_tenant_config(tenant_id=tenant_a, config={"greeting": "Hi A"})
    assert await provider.get_tenant_config(tenant_id=tenant_a) == {"greeting": "Hi A"}
    assert await provider.get_tenant_config(tenant_id=tenant_b) is None


async def test_cache_lock_semantics() -> None:
    provider = MockCacheProvider()
    token = await provider.acquire_lock(name="booking:slot1")
    assert token is not None
    assert await provider.acquire_lock(name="booking:slot1") is None  # held
    assert await provider.release_lock(name="booking:slot1", token="wrong") is False
    assert await provider.release_lock(name="booking:slot1", token=token) is True
    assert await provider.acquire_lock(name="booking:slot1") is not None


async def test_cache_rate_limit_window() -> None:
    provider = MockCacheProvider()
    allowed = [
        await provider.check_rate_limit(key="tenant:x:calls", limit=3, window_seconds=60)
        for _ in range(5)
    ]
    assert allowed == [True, True, True, False, False]


# --- Retry policy ------------------------------------------------------------


async def test_retry_retries_transient_then_succeeds() -> None:
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ProviderTimeoutError("slow")
        return "ok"

    assert await with_retries(flaky) == "ok"
    assert attempts == 3


async def test_retry_does_not_retry_terminal_errors() -> None:
    attempts = 0

    async def broken() -> str:
        nonlocal attempts
        attempts += 1
        raise ProviderResponseError("bad payload")

    with pytest.raises(ProviderResponseError):
        await with_retries(broken)
    assert attempts == 1


async def test_retry_exhaustion_reraises() -> None:
    attempts = 0

    async def always_down() -> str:
        nonlocal attempts
        attempts += 1
        raise ProviderUnavailableError("down")

    with pytest.raises(ProviderError):
        await with_retries(always_down, attempts=3)
    assert attempts == 3
