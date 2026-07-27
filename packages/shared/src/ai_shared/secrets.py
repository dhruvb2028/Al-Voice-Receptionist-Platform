"""Secret resolution.

Configuration values may be given literally (local development) or as a
Secret Manager reference (deployed environments). Call sites ask for a
name and never learn which it was:

    resolver.resolve("GROQ_API_KEY")

A value of the form ``sm://projects/p/secrets/s/versions/latest`` is
fetched from Google Secret Manager; anything else is returned as-is.
Resolved values are cached for the process lifetime — secrets rotate by
deploying, not mid-process, and caching keeps a hot path from making a
network call per request.

Nothing here logs a secret value, and ``__repr__`` is overridden so a
resolver cannot leak one into a traceback.
"""

import os
from typing import Any, Protocol

import structlog

logger = structlog.get_logger()

SECRET_SCHEME = "sm://"  # noqa: S105 - a URI scheme, not a credential


class SecretBackend(Protocol):
    """Fetches a secret payload by resource name."""

    def access(self, resource_name: str) -> str: ...


class GoogleSecretManagerBackend:
    """Thin wrapper over the Secret Manager client.

    Imported lazily so local runs and CI never need the dependency.
    """

    def __init__(self) -> None:
        self._client: Any = None

    def access(self, resource_name: str) -> str:
        if self._client is None:
            from google.cloud import secretmanager

            self._client = secretmanager.SecretManagerServiceClient()
        response = self._client.access_secret_version(name=resource_name)
        payload: bytes = response.payload.data
        return payload.decode("utf-8")


class SecretResolver:
    """Resolves configuration values, transparently dereferencing
    ``sm://`` references."""

    def __init__(self, *, backend: SecretBackend | None = None) -> None:
        self._backend = backend
        self._cache: dict[str, str] = {}

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"<SecretResolver cached={len(self._cache)}>"

    def resolve(self, name: str, default: str | None = None) -> str | None:
        """The value for an environment variable name."""
        if name in self._cache:
            return self._cache[name]
        raw = os.environ.get(name, default)
        if raw is None:
            return None
        value = self.dereference(raw)
        self._cache[name] = value
        return value

    def dereference(self, raw: str) -> str:
        """Resolve one value that may be a secret reference."""
        if not raw.startswith(SECRET_SCHEME):
            return raw
        resource = raw[len(SECRET_SCHEME) :]
        backend = self._backend or GoogleSecretManagerBackend()
        # The resource name is safe to log; the payload never is.
        logger.info("secret_resolved", resource=resource)
        return backend.access(resource)

    def clear_cache(self) -> None:
        self._cache.clear()


_resolver = SecretResolver()


def get_resolver() -> SecretResolver:
    return _resolver


def set_resolver(resolver: SecretResolver) -> None:
    """Test seam."""
    global _resolver
    _resolver = resolver


__all__ = [
    "SECRET_SCHEME",
    "GoogleSecretManagerBackend",
    "SecretBackend",
    "SecretResolver",
    "get_resolver",
    "set_resolver",
]
