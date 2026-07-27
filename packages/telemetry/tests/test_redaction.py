"""Tests for log redaction."""

from ai_telemetry.logging import REDACTED, redact_sensitive


def test_sensitive_keys_redacted() -> None:
    event = {"event": "call", "caller_phone": "+15551234567", "token": "abc"}
    out = redact_sensitive(None, "info", event)
    assert out["caller_phone"] == REDACTED
    assert out["token"] == REDACTED
    assert out["event"] == "call"


def test_phone_numbers_in_free_text_redacted() -> None:
    event = {"event": "note", "message": "callback at +1 (555) 123-4567 please"}
    out = redact_sensitive(None, "info", event)
    assert "555" not in out["message"]
    assert REDACTED in out["message"]


def test_nested_dict_redacted() -> None:
    event = {"event": "x", "payload": {"address": "12 Main St", "kind": "b"}}
    out = redact_sensitive(None, "info", event)
    assert out["payload"]["address"] == REDACTED
    assert out["payload"]["kind"] == "b"


def test_clean_values_untouched() -> None:
    event = {"event": "ok", "count": 3, "name": "greeting"}
    out = redact_sensitive(None, "info", event)
    assert out == {"event": "ok", "count": 3, "name": "greeting"}
