"""Telephony provider interface (Twilio in production)."""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class InboundCall(BaseModel):
    provider_call_sid: str
    from_number: str
    to_number: str


class RecordingMetadata(BaseModel):
    provider_call_sid: str
    recording_sid: str
    duration_seconds: int
    download_url: str


@runtime_checkable
class TelephonyProvider(Protocol):
    """Webhook verification, call control, and media-stream setup."""

    def verify_webhook(self, *, url: str, params: Mapping[str, str], signature: str) -> bool:
        """Validate a webhook signature over the raw request. Must be
        constant-time and must not parse before verifying."""
        ...

    def parse_inbound_call(self, params: Mapping[str, str]) -> InboundCall:
        """Extract the inbound call from verified webhook params."""
        ...

    def stream_response_twiml(self, *, ws_url: str, announcement: str | None) -> str:
        """TwiML (or equivalent) that starts the media stream."""
        ...

    async def send_audio(self, *, call_sid: str, payload_b64: str) -> None: ...

    async def clear_audio(self, *, call_sid: str) -> None:
        """Flush queued playback (barge-in)."""
        ...

    async def transfer_call(self, *, call_sid: str, destination_e164: str) -> None: ...

    async def end_call(self, *, call_sid: str) -> None: ...

    async def fetch_recording(self, *, call_sid: str) -> RecordingMetadata | None: ...


class MockTelephonyProvider:
    """In-memory telephony for tests and the browser simulator."""

    def __init__(self) -> None:
        self.sent_audio: list[tuple[str, str]] = []
        self.cleared: list[str] = []
        self.transfers: list[tuple[str, str]] = []
        self.ended: list[str] = []
        self.recordings: dict[str, RecordingMetadata] = {}
        self.valid_signature = "valid-signature"

    def verify_webhook(self, *, url: str, params: Mapping[str, str], signature: str) -> bool:
        return signature == self.valid_signature

    def parse_inbound_call(self, params: Mapping[str, str]) -> InboundCall:
        return InboundCall(
            provider_call_sid=params["CallSid"],
            from_number=params["From"],
            to_number=params["To"],
        )

    def stream_response_twiml(self, *, ws_url: str, announcement: str | None) -> str:
        say = f"<Say>{announcement}</Say>" if announcement else ""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{say}<Connect><Stream url="{ws_url}"/></Connect></Response>'
        )

    async def send_audio(self, *, call_sid: str, payload_b64: str) -> None:
        self.sent_audio.append((call_sid, payload_b64))

    async def clear_audio(self, *, call_sid: str) -> None:
        self.cleared.append(call_sid)
        self.sent_audio = [(sid, p) for sid, p in self.sent_audio if sid != call_sid]

    async def transfer_call(self, *, call_sid: str, destination_e164: str) -> None:
        self.transfers.append((call_sid, destination_e164))

    async def end_call(self, *, call_sid: str) -> None:
        self.ended.append(call_sid)

    async def fetch_recording(self, *, call_sid: str) -> RecordingMetadata | None:
        return self.recordings.get(call_sid)
