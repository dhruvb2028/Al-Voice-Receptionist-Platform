"""Voice-service settings. Fails fast on missing required values."""

from functools import lru_cache

from ai_providers.groq import GroqConfig
from ai_shared.settings import BaseServiceSettings


class VoiceSettings(BaseServiceSettings):
    service_name: str = "voice"

    # Provider credentials become required at the telephony milestone.
    database_url: str | None = None
    deepgram_api_key: str | None = None
    cartesia_api_key: str | None = None

    # --- Groq LLM (all environment-driven; never hardcoded) ---
    groq_api_key: str | None = None
    #: fast model for live turns
    groq_model: str = "llama-3.3-70b-versatile"
    #: used for the final retry attempt when the live model fails
    groq_fallback_model: str | None = "llama-3.1-8b-instant"
    #: model for post-call summarization (defaults to the live model)
    groq_post_call_model: str | None = None
    groq_temperature: float = 0.3
    groq_max_output_tokens: int = 300
    groq_timeout_seconds: float = 8.0
    groq_max_retries: int = 2

    def build_groq_config(self) -> GroqConfig:
        if not self.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is required for live LLM turns.")
        return GroqConfig(
            api_key=self.groq_api_key,
            live_model=self.groq_model,
            fallback_model=self.groq_fallback_model,
            post_call_model=self.groq_post_call_model,
            temperature=self.groq_temperature,
            max_tokens=self.groq_max_output_tokens,
            timeout_seconds=self.groq_timeout_seconds,
            max_retries=self.groq_max_retries,
        )


@lru_cache
def get_settings() -> VoiceSettings:
    return VoiceSettings()
