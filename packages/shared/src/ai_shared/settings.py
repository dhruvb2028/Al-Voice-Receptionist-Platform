"""Base settings shared by every service.

Each service subclasses :class:`BaseServiceSettings` and adds its own
variables. Instantiation fails fast on missing or malformed values so a
misconfigured service never starts serving traffic.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


class BaseServiceSettings(BaseSettings):
    """Settings common to api, voice, and worker services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    sentry_dsn: str | None = None
    sentry_release: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache
def get_base_settings() -> BaseServiceSettings:
    return BaseServiceSettings()
