"""Worker-service settings. Fails fast on missing required values."""

from functools import lru_cache

from ai_shared.settings import BaseServiceSettings


class WorkerSettings(BaseServiceSettings):
    service_name: str = "worker"

    database_url: str | None = None
    qstash_current_signing_key: str | None = None
    qstash_next_signing_key: str | None = None
    resend_api_key: str | None = None


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
