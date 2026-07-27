"""Worker-service settings. Fails fast on missing required values."""

from functools import lru_cache

from ai_shared.settings import BaseServiceSettings


class WorkerSettings(BaseServiceSettings):
    service_name: str = "worker"

    database_url: str | None = None
    qstash_current_signing_key: str | None = None
    qstash_next_signing_key: str | None = None
    resend_api_key: str | None = None
    #: public base URL QStash delivers to (signature `sub` claim)
    worker_base_url: str | None = None

    # Groq (post-call extraction; latency-insensitive, cheap model)
    groq_api_key: str | None = None
    groq_post_call_model: str = "llama-3.1-8b-instant"
    groq_timeout_seconds: float = 30.0
    groq_max_retries: int = 2

    # Twilio (recording fetch + provider-copy deletion)
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    #: delete the provider copy once stored in R2
    delete_provider_recordings: bool = True

    # Cloudflare R2
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    r2_endpoint: str | None = None


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
