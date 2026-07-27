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


def _redact(**fields: object) -> dict[str, object]:
    return dict(redact_sensitive(None, "info", dict(fields)))


def test_key_fragments_catch_new_field_names() -> None:
    """A field added later named *_api_key or *_token is covered without
    editing the allowlist by hand."""
    out = _redact(groq_api_key="gsk_live", refresh_token="rt_live", twilio_auth="secret")
    assert out == {
        "groq_api_key": REDACTED,
        "refresh_token": REDACTED,
        "twilio_auth": REDACTED,
    }


def test_emails_in_free_text_are_scrubbed() -> None:
    out = _redact(event="forward to owner@example.com please")
    assert "owner@example.com" not in str(out["event"])
    assert REDACTED in str(out["event"])


def test_deeply_nested_values_are_redacted() -> None:
    out = _redact(a={"b": {"c": {"api_key": "gsk_live"}}})
    assert out["a"]["b"]["c"]["api_key"] == REDACTED  # type: ignore[index]


def test_lists_of_dicts_are_redacted() -> None:
    out = _redact(items=[{"phone": "+15551234821"}, {"service": "Leak Repair"}])
    assert out["items"] == [{"phone": REDACTED}, {"service": "Leak Repair"}]


def test_message_bodies_and_notes_are_redacted() -> None:
    out = _redact(body="the caller's full message", internal_note="staff only")
    assert out == {"body": REDACTED, "internal_note": REDACTED}


def test_masked_fragments_are_kept() -> None:
    """Masked forms are the safe representation we deliberately log."""
    out = _redact(recipient_masked="4821 masked", from_number_last_four="4821")
    assert out["from_number_last_four"] == "4821"
