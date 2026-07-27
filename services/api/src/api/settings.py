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

    @property
    def platform_admin_user_id_list(self) -> frozenset[str]:
        return frozenset(
            part.strip() for part in self.platform_admin_user_ids.split(",") if part.strip()
        )


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
