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


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
