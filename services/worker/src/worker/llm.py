"""Post-call LLM access.

Extraction is not latency-sensitive, so the worker runs the cheaper
post-call model with a generous timeout rather than the live-turn model.
"""

from ai_providers.llm import LLMProvider

from worker.settings import get_settings

_llm: LLMProvider | None = None


def get_llm() -> LLMProvider:
    global _llm
    if _llm is None:
        from ai_providers.groq import GroqConfig, GroqLLMProvider

        settings = get_settings()
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is required for post-call extraction.")
        _llm = GroqLLMProvider(
            GroqConfig(
                api_key=settings.groq_api_key,
                live_model=settings.groq_post_call_model,
                temperature=0.0,
                max_tokens=600,
                timeout_seconds=settings.groq_timeout_seconds,
                max_retries=settings.groq_max_retries,
            )
        )
    return _llm


def set_llm(provider: LLMProvider | None) -> None:
    """Test seam."""
    global _llm
    _llm = provider
