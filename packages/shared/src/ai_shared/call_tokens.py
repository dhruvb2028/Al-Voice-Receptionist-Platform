"""Signed, single-use call tokens.

The API mints one per inbound call and embeds it in the media-stream
URL; the voice service validates it at WebSocket open. Bound to the
call SID and tenant, short-lived, and single-use — a leaked URL is
worthless after the call starts.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid

TOKEN_TTL_SECONDS = 300


class CallTokenError(Exception):
    pass


def mint_call_token(
    *,
    call_sid: str,
    tenant_id: str,
    signing_key: str,
    ttl_seconds: int = TOKEN_TTL_SECONDS,
) -> str:
    body = json.dumps(
        {
            "call_sid": call_sid,
            "tenant_id": tenant_id,
            "exp": int(time.time()) + ttl_seconds,
            "jti": uuid.uuid4().hex,
        },
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
    signature = hmac.new(signing_key.encode(), body, hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_call_token(token: str, *, signing_key: str) -> dict[str, str]:
    """Validate signature and expiry; returns the claims. Single-use
    enforcement (jti tracking) is the caller's responsibility, since it
    needs shared state."""
    try:
        encoded, signature = token.rsplit(".", 1)
        body = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, TypeError) as exc:
        raise CallTokenError("malformed token") from exc
    expected = hmac.new(signing_key.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise CallTokenError("bad signature")
    claims = json.loads(body)
    if int(claims.get("exp", 0)) < time.time():
        raise CallTokenError("token expired")
    if not claims.get("call_sid") or not claims.get("tenant_id") or not claims.get("jti"):
        raise CallTokenError("missing claims")
    return {
        "call_sid": str(claims["call_sid"]),
        "tenant_id": str(claims["tenant_id"]),
        "jti": str(claims["jti"]),
    }
