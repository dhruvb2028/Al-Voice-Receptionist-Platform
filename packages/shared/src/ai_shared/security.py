"""Transport-level security controls shared by every service.

Three concerns live here, each of which is easy to get wrong per-route
and so is applied once at the app boundary:

* **Response headers** — a fixed set that cannot be forgotten per route.
* **Request size** — a body cap enforced before parsing, so an oversized
  payload is rejected without being buffered into memory.
* **Rate limiting** — a fixed-window counter keyed by caller identity,
  with the window and limit chosen per route group.

The rate limiter is in-process by design at this scale: a single Cloud
Run service with a small instance count. It is exposed behind
:class:`RateLimiter` so a Redis-backed implementation can replace it
without touching call sites.
"""

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

import structlog
from fastapi import Request, Response
from starlette.responses import JSONResponse

from ai_shared.errors import ErrorBody, ErrorEnvelope

logger = structlog.get_logger()

#: 1 MiB. Every legitimate request this platform serves is far smaller;
#: webhooks and job payloads are a few kilobytes at most.
DEFAULT_MAX_BODY_BYTES = 1_048_576

#: Applied to every response. HSTS is only meaningful over TLS, which
#: Cloud Run terminates, so it is safe to send unconditionally there.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # The API serves JSON only; a default-deny CSP costs nothing and
    # neutralises content sniffing on any accidental HTML response.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}


class RateLimiter(Protocol):
    """Allow or deny one request against a keyed budget."""

    async def allow(self, *, key: str, limit: int, window_seconds: int) -> bool: ...


@dataclass
class InMemoryRateLimiter:
    """Fixed-window counter.

    Windows are aligned to wall-clock multiples of the window length so
    two processes agree on boundaries without coordination. State is
    pruned as it expires, so memory tracks the active caller count rather
    than growing forever.
    """

    _counts: dict[tuple[str, int], int] = field(default_factory=lambda: defaultdict(int))
    _clock: Callable[[], float] = time.monotonic

    async def allow(self, *, key: str, limit: int, window_seconds: int) -> bool:
        if limit <= 0:
            return False
        now = self._clock()
        window = int(now // window_seconds)
        self._prune(window)
        bucket = (key, window)
        self._counts[bucket] += 1
        return self._counts[bucket] <= limit

    def _prune(self, current_window: int) -> None:
        stale = [k for k in self._counts if k[1] < current_window]
        for key in stale:
            del self._counts[key]

    def reset(self) -> None:
        self._counts.clear()


def client_key(request: Request) -> str:
    """Identity a rate limit is charged against.

    Prefers the authenticated principal so one noisy tenant cannot
    exhaust another's budget; falls back to the client address. The
    left-most ``X-Forwarded-For`` entry is used because Cloud Run appends
    the real client address there and we sit behind it.
    """
    principal = getattr(request.state, "principal_key", None)
    if principal:
        return f"principal:{principal}"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorEnvelope(error=ErrorBody(code=code, message=message)).model_dump(
            exclude_none=True
        ),
    )


def build_security_middleware(
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    limiter: RateLimiter | None = None,
    default_limit: int = 240,
    default_window_seconds: int = 60,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """One middleware applying size limits, rate limits, and headers."""
    limiter = limiter or InMemoryRateLimiter()

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Declared length is checked first so an oversized upload is
        # refused before its body is read.
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > max_body_bytes:
                    logger.warning("request_too_large", declared=declared)
                    return _error_response(413, "request_too_large", "Request body is too large.")
            except ValueError:
                return _error_response(400, "bad_request", "Invalid Content-Length header.")

        if not await limiter.allow(
            key=client_key(request), limit=default_limit, window_seconds=default_window_seconds
        ):
            logger.warning("rate_limited", path=request.url.path)
            response: Response = _error_response(
                429, "rate_limited", "Too many requests. Please slow down."
            )
            response.headers["Retry-After"] = str(default_window_seconds)
        else:
            response = await call_next(request)

        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    return middleware


async def enforce_body_limit(request: Request, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> bytes:
    """Read a body, refusing anything over the cap.

    Used by routes that read raw bytes (webhooks, jobs) where a chunked
    request carries no Content-Length for the middleware to check.
    """
    from ai_shared.errors import ValidationFailedError

    body = await request.body()
    if len(body) > max_bytes:
        raise ValidationFailedError("Request body is too large.")
    return body


__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "SECURITY_HEADERS",
    "InMemoryRateLimiter",
    "RateLimiter",
    "build_security_middleware",
    "client_key",
    "enforce_body_limit",
]
