"""Request-ID generation and propagation.

Every inbound HTTP request gets a ``req_``-prefixed ULID-like ID (or
reuses a valid incoming ``X-Request-ID``). The ID lives in a context
variable so logging can attach it without threading it through call
signatures.
"""

import re
import secrets
import time
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_current_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def generate_request_id(prefix: str = "req") -> str:
    """Time-sortable, collision-resistant ID: ``req_<ms-hex>_<random>``."""
    ms = int(time.time() * 1000)
    return f"{prefix}_{ms:x}{secrets.token_hex(6)}"


def sanitize_incoming_request_id(raw: str | None) -> str | None:
    """Accept a caller-supplied request ID only if it is shaped safely."""
    if raw and _REQUEST_ID_RE.match(raw):
        return raw
    return None


def set_request_id(request_id: str) -> None:
    _current_request_id.set(request_id)


def get_request_id() -> str | None:
    return _current_request_id.get()
