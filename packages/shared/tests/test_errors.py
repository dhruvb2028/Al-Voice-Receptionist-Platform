"""Tests for the standard error envelope."""

from ai_shared.errors import ErrorDetail, NotFoundError, PlatformError


def test_error_envelope_shape() -> None:
    err = NotFoundError("Tenant not found")
    envelope = err.to_envelope(request_id="req_test123")
    assert envelope == {
        "error": {
            "code": "not_found",
            "message": "Tenant not found",
            "request_id": "req_test123",
            "details": [],
        }
    }


def test_error_details_serialize() -> None:
    err = PlatformError(
        "Bad config",
        details=[ErrorDetail(field="greeting", issue="must not be empty")],
    )
    envelope = err.to_envelope()
    assert envelope["error"]["details"] == [{"field": "greeting", "issue": "must not be empty"}]


def test_status_codes() -> None:
    assert NotFoundError("x").status_code == 404
    assert PlatformError("x").status_code == 500
