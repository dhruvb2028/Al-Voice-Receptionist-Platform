"""Provider error taxonomy.

Every adapter maps vendor-specific failures onto these types so callers
branch on semantics, never on vendor error codes. The voice fallback
ladder keys off this hierarchy: transient errors may retry (bounded),
terminal errors degrade immediately.
"""


class ProviderError(Exception):
    """Base for all provider failures."""

    #: safe, machine-readable category for logs and metrics
    category: str = "provider_error"
    #: transient errors are eligible for bounded retry
    transient: bool = False

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class ProviderTimeoutError(ProviderError):
    category = "timeout"
    transient = True


class ProviderUnavailableError(ProviderError):
    """Connection refused, 5xx, stream dropped."""

    category = "unavailable"
    transient = True


class ProviderRateLimitError(ProviderError):
    category = "rate_limited"
    transient = True


class ProviderAuthError(ProviderError):
    """Bad or expired credentials — never retried."""

    category = "auth"
    transient = False


class CredentialRevokedError(ProviderAuthError):
    """The tenant revoked our access (e.g. Google OAuth). Requires
    reconnection by an admin, not a retry."""

    category = "credential_revoked"


class ProviderResponseError(ProviderError):
    """The provider answered with something we cannot parse or that
    violates its contract. Fail closed; never guess."""

    category = "bad_response"
    transient = False


class DuplicateSendError(ProviderError):
    """An idempotency key was already used — the original send stands."""

    category = "duplicate_send"
    transient = False
