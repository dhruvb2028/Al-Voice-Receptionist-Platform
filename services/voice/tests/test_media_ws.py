"""Media-stream WebSocket tests: token auth, event flow, sequence and
duplicate handling, backpressure, and barge-in clear."""

import base64
import json
import os
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from ai_shared.call_tokens import mint_call_token
from fastapi.testclient import TestClient
from voice.media_ws import (
    AUDIO_QUEUE_MAX_FRAMES,
    AudioBridge,
    reset_ws_state,
)

SIGNING_KEY = "ws-test-signing-key"


@pytest.fixture
def client() -> Iterator[TestClient]:
    from voice.main import create_app
    from voice.settings import get_settings

    get_settings.cache_clear()
    os.environ["CALL_TOKEN_SIGNING_KEY"] = SIGNING_KEY
    os.environ.pop("DATABASE_URL", None)
    get_settings.cache_clear()
    reset_ws_state()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    os.environ.pop("CALL_TOKEN_SIGNING_KEY", None)
    get_settings.cache_clear()
    reset_ws_state()


def _token(call_sid: str = "CA_ws_test") -> str:
    return mint_call_token(call_sid=call_sid, tenant_id="tenant-ws", signing_key=SIGNING_KEY)


def _media_event(sequence: int, payload: bytes = b"\x00" * 160) -> str:
    return json.dumps(
        {
            "event": "media",
            "media": {
                "payload": base64.b64encode(payload).decode(),
                "sequenceNumber": str(sequence),
            },
        }
    )


# --- authentication ----------------------------------------------------------


def test_missing_token_rejected(client: TestClient) -> None:
    with pytest.raises(Exception), client.websocket_connect("/ws"):  # noqa: B017
        pass


def test_forged_token_rejected(client: TestClient) -> None:
    bad = mint_call_token(call_sid="CA_x", tenant_id="t", signing_key="wrong-key")
    with pytest.raises(Exception), client.websocket_connect(f"/ws?token={bad}"):  # noqa: B017
        pass


def test_token_single_use(client: TestClient) -> None:
    token = _token("CA_once")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_text(json.dumps({"event": "stop"}))
    # Reusing the same token must be rejected.
    with pytest.raises(Exception), client.websocket_connect(f"/ws?token={token}"):  # noqa: B017
        pass


# --- event flow --------------------------------------------------------------


def test_start_media_stop_flow(client: TestClient) -> None:
    token = _token("CA_flow")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_text(
            json.dumps(
                {
                    "event": "start",
                    "streamSid": "MZ123",
                    "start": {"callSid": "CA_flow"},
                }
            )
        )
        ws.send_text(_media_event(1))
        ws.send_text(_media_event(2))
        ws.send_text(json.dumps({"event": "stop"}))
        # Connection closes cleanly after stop.


def test_callsid_mismatch_terminates(client: TestClient) -> None:
    token = _token("CA_real")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_text(
            json.dumps(
                {
                    "event": "start",
                    "streamSid": "MZ1",
                    "start": {"callSid": "CA_spoofed"},
                }
            )
        )
        # Server ends the stream on mismatch.
        with pytest.raises(Exception):  # noqa: B017
            for _ in range(10):
                ws.send_text(_media_event(1))
            ws.receive_text()


# --- bridge unit behavior ----------------------------------------------------


def _bridge() -> AudioBridge:
    return AudioBridge(websocket=AsyncMock())


def test_duplicate_and_gap_sequences() -> None:
    bridge = _bridge()
    bridge.push_inbound(base64.b64encode(b"a").decode(), 1)
    bridge.push_inbound(base64.b64encode(b"a").decode(), 1)  # duplicate
    bridge.push_inbound(base64.b64encode(b"b").decode(), 5)  # gap
    assert bridge.frames_received == 2
    assert bridge.duplicate_frames == 1
    assert bridge.last_sequence == 5


def test_backpressure_drops_oldest() -> None:
    bridge = _bridge()
    for i in range(AUDIO_QUEUE_MAX_FRAMES + 25):
        bridge.push_inbound(base64.b64encode(bytes([i % 256])).decode(), i + 1)
    assert bridge.frames_dropped == 25
    assert bridge.inbound.qsize() == AUDIO_QUEUE_MAX_FRAMES
    # Oldest frames were discarded — the queue starts at frame 26.
    first = bridge.inbound.get_nowait()
    assert first == bytes([25])


async def test_clear_audio_sends_clear_event() -> None:
    ws = AsyncMock()
    bridge = AudioBridge(websocket=ws, stream_sid="MZ9")
    await bridge.clear_audio()
    sent = json.loads(ws.send_text.call_args[0][0])
    assert sent == {"event": "clear", "streamSid": "MZ9"}


async def test_send_audio_wraps_media_event() -> None:
    ws = AsyncMock()
    bridge = AudioBridge(websocket=ws, stream_sid="MZ9")
    await bridge.send_audio(b"\x01\x02")
    sent = json.loads(ws.send_text.call_args[0][0])
    assert sent["event"] == "media"
    assert base64.b64decode(sent["media"]["payload"]) == b"\x01\x02"


async def test_send_before_start_is_noop() -> None:
    ws = AsyncMock()
    bridge = AudioBridge(websocket=ws)  # no stream yet
    await bridge.send_audio(b"\x01")
    await bridge.clear_audio()
    ws.send_text.assert_not_called()


def test_invalid_connections_rate_limited(client: TestClient) -> None:
    from voice.media_ws import INVALID_CONNECTION_LIMIT, _too_many_invalid

    ip = "203.0.113.9"
    allowed = [not _too_many_invalid(ip) for _ in range(INVALID_CONNECTION_LIMIT + 3)]
    assert allowed[:INVALID_CONNECTION_LIMIT] == [True] * INVALID_CONNECTION_LIMIT
    assert allowed[INVALID_CONNECTION_LIMIT:] == [False, False, False]
