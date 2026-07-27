"""Clerk session-token verification.

Browser claims are never trusted without verification: every token's
RS256 signature is checked against Clerk's JWKS, plus issuer, audience,
expiry, and not-before. JWKS keys are cached with a TTL and refreshed on
unknown ``kid`` (rotation).
"""

import time
from typing import Any

import httpx
import jwt
import structlog
from ai_shared.errors import UnauthorizedError
from jwt import InvalidTokenError, PyJWK
from jwt.types import Options

from api.auth.models import ClerkClaims

logger = structlog.get_logger()

_JWKS_TTL_SECONDS = 300


class JwksCache:
    """Fetches and caches Clerk's JWKS."""

    def __init__(self, jwks_url: str) -> None:
        self._jwks_url = jwks_url
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at = 0.0

    async def get_key(self, kid: str) -> PyJWK:
        stale = (time.monotonic() - self._fetched_at) > _JWKS_TTL_SECONDS
        if kid not in self._keys or stale:
            try:
                await self._refresh()
            except httpx.HTTPError as exc:
                if kid in self._keys:
                    # Serve the cached key when the refresh endpoint is
                    # briefly unavailable; never fail a live request for
                    # a key we already trust.
                    logger.warning("jwks_refresh_failed", error=str(exc))
                else:
                    raise UnauthorizedError("Unable to verify token signing keys.") from exc
        try:
            return self._keys[kid]
        except KeyError as exc:
            raise UnauthorizedError("Unknown signing key.") from exc

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self._jwks_url)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        self._keys = {key["kid"]: PyJWK(key) for key in payload.get("keys", []) if key.get("kid")}
        self._fetched_at = time.monotonic()
        logger.debug("jwks_refreshed", key_count=len(self._keys))

    def prime(self, keys: dict[str, PyJWK]) -> None:
        """Inject keys directly (tests)."""
        self._keys = keys
        self._fetched_at = time.monotonic()


class TokenVerifier:
    def __init__(
        self,
        *,
        jwks: JwksCache,
        issuer: str,
        audience: str | None,
    ) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._audience = audience

    async def verify(self, token: str) -> ClerkClaims:
        try:
            unverified = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise UnauthorizedError("Malformed token.") from exc

        kid = unverified.get("kid")
        if not isinstance(kid, str) or not kid:
            raise UnauthorizedError("Token missing key ID.")

        key = await self._jwks.get_key(kid)
        options: Options = {
            "require": ["exp", "iat", "sub"],
            "verify_aud": self._audience is not None,
        }
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                options=options,
                leeway=5,
            )
        except InvalidTokenError as exc:
            # One retry path: key rotation between cache refreshes is
            # handled inside JwksCache; anything else is a hard reject.
            raise UnauthorizedError("Invalid or expired token.") from exc

        return ClerkClaims(
            sub=str(payload["sub"]),
            org_id=payload.get("org_id"),
            org_role=payload.get("org_role"),
            platform_role=payload.get("platform_role"),
        )
