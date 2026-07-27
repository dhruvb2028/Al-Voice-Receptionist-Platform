"""API-service settings. Fails fast on missing required values."""

from functools import lru_cache

from ai_shared.settings import BaseServiceSettings


class ApiSettings(BaseServiceSettings):
    service_name: str = "api"

    # Required once the schema lands; optional in the bootstrap milestone
    # so the service can start before infrastructure is provisioned.
    database_url: str | None = None
    upstash_redis_rest_url: str | None = None
    upstash_redis_rest_token: str | None = None

    # Clerk authentication. Issuer + audience pin token verification;
    # jwks url derives from the issuer unless overridden.
    clerk_secret_key: str | None = None
    clerk_webhook_secret: str | None = None
    clerk_jwt_issuer: str | None = None
    clerk_jwt_audience: str | None = None
    clerk_jwks_url: str | None = None

    # Comma-separated Clerk user IDs granted platform_admin, as a
    # fallback to the platform_role JWT claim.
    platform_admin_user_ids: str = ""

    # Google Calendar OAuth
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_oauth_redirect_uri: str | None = None

    # Signing key shared with call tokens; also signs OAuth state.
    call_token_signing_key: str | None = None

    # Application-layer encryption (base64-encoded 32-byte keys).
    data_encryption_key: str | None = None
    lookup_hash_key: str | None = None

    # Where OAuth callbacks redirect the admin's browser.
    dashboard_base_url: str | None = None

    # Telephony
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_webhook_base_url: str | None = None
    voice_ws_base_url: str | None = None
    #: platform-wide concurrent phone-call cap (2 calls x 3 voice instances)
    max_concurrent_calls: int = 6

    # Cloudflare R2 (signed recording URLs)
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    r2_endpoint: str | None = None

    @property
    def platform_admin_user_id_list(self) -> frozenset[str]:
        return frozenset(
            part.strip() for part in self.platform_admin_user_ids.split(",") if part.strip()
        )


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
