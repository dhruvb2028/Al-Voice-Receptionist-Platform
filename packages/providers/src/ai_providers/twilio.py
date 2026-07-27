"""Twilio telephony: webhook signature verification, TwiML, and REST
call control.

Signature scheme (Twilio's spec): base64(HMAC-SHA1(auth_token,
full_url + concat(sorted(params as key+value)))). Verification runs
before any parsing and is constant-time.
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping
from xml.sax.saxutils import escape

import httpx
import structlog

from ai_providers.errors import ProviderResponseError, ProviderUnavailableError
from ai_providers.telephony import InboundCall, RecordingMetadata

logger = structlog.get_logger()

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def compute_twilio_signature(*, auth_token: str, url: str, params: Mapping[str, str]) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1)
    return base64.b64encode(digest.digest()).decode()


class TwilioTelephonyProvider:
    """Webhook verification, TwiML generation, and REST call actions."""

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._http = http or httpx.AsyncClient(
            base_url=f"{TWILIO_API_BASE}/Accounts/{account_sid}",
            auth=(account_sid, auth_token),
            timeout=10.0,
        )

    # -- webhook ------------------------------------------------------------

    def verify_webhook(self, *, url: str, params: Mapping[str, str], signature: str) -> bool:
        expected = compute_twilio_signature(auth_token=self._auth_token, url=url, params=params)
        return hmac.compare_digest(expected, signature)

    def parse_inbound_call(self, params: Mapping[str, str]) -> InboundCall:
        try:
            return InboundCall(
                provider_call_sid=params["CallSid"],
                from_number=params["From"],
                to_number=params["To"],
            )
        except KeyError as exc:
            raise ProviderResponseError(f"webhook missing field {exc}") from exc

    # -- TwiML ---------------------------------------------------------------

    def stream_response_twiml(self, *, ws_url: str, announcement: str | None) -> str:
        say = f"<Say>{escape(announcement)}</Say>" if announcement else ""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{say}<Connect><Stream url="{escape(ws_url)}"/></Connect></Response>'
        )

    def unavailable_twiml(self, message: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Say>{escape(message)}</Say><Hangup/></Response>"
        )

    # -- REST call control ---------------------------------------------------

    async def transfer_call(self, *, call_sid: str, destination_e164: str) -> None:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Dial>{escape(destination_e164)}</Dial></Response>"
        )
        response = await self._http.post(f"/Calls/{call_sid}.json", data={"Twiml": twiml})
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"transfer failed ({response.status_code})", provider="twilio"
            )

    async def end_call(self, *, call_sid: str) -> None:
        response = await self._http.post(f"/Calls/{call_sid}.json", data={"Status": "completed"})
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"end call failed ({response.status_code})", provider="twilio"
            )

    async def fetch_recording(self, *, call_sid: str) -> RecordingMetadata | None:
        response = await self._http.get(
            "/Recordings.json", params={"CallSid": call_sid, "PageSize": 1}
        )
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"recording lookup failed ({response.status_code})", provider="twilio"
            )
        recordings = response.json().get("recordings", [])
        if not recordings:
            return None
        recording = recordings[0]
        return RecordingMetadata(
            provider_call_sid=call_sid,
            recording_sid=recording["sid"],
            duration_seconds=int(recording.get("duration") or 0),
            download_url=f"{TWILIO_API_BASE}/Accounts/{self._account_sid}"
            f"/Recordings/{recording['sid']}.wav",
        )
