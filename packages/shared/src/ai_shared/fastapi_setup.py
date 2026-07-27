"""Standard FastAPI wiring shared by all services.

``configure_service_app`` attaches the platform conventions every service
must have: request-ID middleware, the standard error envelope for all
failure paths, and a ``/healthz`` endpoint for Cloud Run health checks.
"""

from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ai_shared.errors import ErrorBody, ErrorDetail, ErrorEnvelope, PlatformError
from ai_shared.request_id import (
    REQUEST_ID_HEADER,
    generate_request_id,
    get_request_id,
    sanitize_incoming_request_id,
    set_request_id,
)
from ai_shared.security import (
    DEFAULT_MAX_BODY_BYTES,
    RateLimiter,
    build_security_middleware,
)

logger = structlog.get_logger()


def configure_service_app(
    app: FastAPI,
    *,
    service_name: str,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    rate_limit: int = 240,
    rate_limit_window_seconds: int = 60,
    limiter: RateLimiter | None = None,
) -> FastAPI:
    """Attach the platform middleware, error handlers, and health endpoint.

    Middleware order matters: Starlette runs the most recently added
    first, so the security layer is registered *before* the request-ID
    layer in order to run inside it. That way a request rejected for
    size or rate still gets a request id on its response and is
    traceable in the logs.
    """
    app.middleware("http")(
        build_security_middleware(
            max_body_bytes=max_body_bytes,
            limiter=limiter,
            default_limit=rate_limit,
            default_window_seconds=rate_limit_window_seconds,
        )
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = (
            sanitize_incoming_request_id(request.headers.get(REQUEST_ID_HEADER))
            or generate_request_id()
        )
        set_request_id(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id, service=service_name)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id", "service")
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(PlatformError)
    async def platform_error_handler(request: Request, exc: PlatformError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_envelope(request_id=get_request_id()),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            ErrorDetail(
                field=".".join(str(loc) for loc in err.get("loc", []) if loc != "body"),
                issue=str(err.get("msg", "invalid")),
            )
            for err in exc.errors()
        ]
        envelope = ErrorEnvelope(
            error=ErrorBody(
                code="validation_failed",
                message="Request validation failed.",
                request_id=get_request_id(),
                details=details,
            )
        )
        return JSONResponse(status_code=422, content=envelope.model_dump(exclude_none=True))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", path=request.url.path)
        envelope = ErrorEnvelope(
            error=ErrorBody(
                code="internal_error",
                message="An internal error occurred.",
                request_id=get_request_id(),
            )
        )
        return JSONResponse(status_code=500, content=envelope.model_dump(exclude_none=True))

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": service_name}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> dict[str, str]:
        # Startup probe target. Dependency checks (database, redis) attach
        # here as those clients land; returning 200 means "safe to route".
        return {"status": "ready", "service": service_name}

    return app
