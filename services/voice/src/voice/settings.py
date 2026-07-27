"""Voice-service settings. Fails fast on missing required values."""

from functools import lru_cache

from ai_shared.settings import BaseServiceSettings


class VoiceSettings(BaseServiceSettings):
    service_name: str = "voice"

    # Provider credentials become required at the telephony milestone.
    database_url: str | None = None
    deepgram_api_key: str | None = None
    groq_api_key: str | None = None
    cartesia_api_key: str | None = None


@lru_cache
def get_settings() -> VoiceSettings:
    return VoiceSettings()
