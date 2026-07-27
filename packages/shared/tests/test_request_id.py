"""Tests for request-ID generation and sanitization."""

from ai_shared.request_id import (
    generate_request_id,
    get_request_id,
    sanitize_incoming_request_id,
    set_request_id,
)


def test_generated_ids_are_unique_and_prefixed() -> None:
    ids = {generate_request_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("req_") for i in ids)


def test_sanitize_accepts_safe_ids() -> None:
    assert sanitize_incoming_request_id("req_abc12345") == "req_abc12345"


def test_sanitize_rejects_unsafe_ids() -> None:
    assert sanitize_incoming_request_id("bad id!!") is None
    assert sanitize_incoming_request_id("short") is None
    assert sanitize_incoming_request_id("x" * 65) is None
    assert sanitize_incoming_request_id(None) is None


def test_contextvar_roundtrip() -> None:
    set_request_id("req_ctx_test")
    assert get_request_id() == "req_ctx_test"
