"""QStash delivery verification.

Every job endpoint verifies the ``Upstash-Signature`` JWT against the
current signing key, falling back to the next key so rotation never
drops deliveries. The signature covers the destination URL and a hash
of the body; expiry bounds replay.
"""

import base64
import hashlib
import hmac
import json
import time

import structlog

logger = structlog.get_logger()


class QStashVerificationError(Exception):
    pass


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _verify_with_key(token: str, *, key: str, url: str, body: bytes) -> None:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise QStashVerificationError("malformed signature token") from exc

    expected = hmac.new(
        key.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256
    ).digest()
    provided = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected, provided):
        raise QStashVerificationError("signature mismatch")

    claims = json.loads(_b64url_decode(payload_b64))
    if int(claims.get("exp", 0)) < time.time():
        raise QStashVerificationError("token expired")
    if claims.get("sub") != url:
        raise QStashVerificationError("url mismatch")
    body_hash = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
    claimed = str(claims.get("body", "")).rstrip("=")
    if claimed and not hmac.compare_digest(claimed, body_hash):
        raise QStashVerificationError("body hash mismatch")


def verify_qstash_signature(
    token: str,
    *,
    url: str,
    body: bytes,
    current_key: str,
    next_key: str | None = None,
) -> None:
    """Raise QStashVerificationError unless the delivery is authentic."""
    try:
        _verify_with_key(token, key=current_key, url=url, body=body)
        return
    except QStashVerificationError:
        if not next_key:
            raise
    _verify_with_key(token, key=next_key, url=url, body=body)
