"""Standard error format returned by every service.

All HTTP errors serialize to the same envelope so the dashboard and
integrations can rely on one shape:

    {
        "error": {
            "code": "tenant_not_found",
            "message": "Human-readable explanation",
            "request_id": "req_...",
            "details": [{"field": "greeting", "issue": "must not be empty"}]
        }
    }
"""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    field: str | None = None
    issue: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class PlatformError(Exception):
    """Base class for typed application errors.

    Subclasses declare a stable machine-readable ``code`` and an HTTP
    status. Handlers convert them into :class:`ErrorEnvelope` responses;
    nothing outside this hierarchy should be raised for expected failures.
    """

    code: str = "internal_error"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []

    def to_envelope(self, request_id: str | None = None) -> dict[str, Any]:
        return ErrorEnvelope(
            error=ErrorBody(
                code=self.code,
                message=self.message,
                request_id=request_id,
                details=self.details,
            )
        ).model_dump(exclude_none=True)


class NotFoundError(PlatformError):
    code = "not_found"
    status_code = 404


class ValidationFailedError(PlatformError):
    code = "validation_failed"
    status_code = 422


class UnauthorizedError(PlatformError):
    code = "unauthorized"
    status_code = 401


class ForbiddenError(PlatformError):
    code = "forbidden"
    status_code = 403


class ConflictError(PlatformError):
    code = "conflict"
    status_code = 409
