"""Standard retry and timeout policy for provider calls.

One policy, used by every adapter: bounded attempts, exponential
backoff, retry only on ``transient`` provider errors. Call-time paths
use the tight policy; background paths may use the standard one.
"""

from collections.abc import Awaitable, Callable

from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ai_providers.errors import ProviderError

#: seconds — call-time operations must fail fast enough to degrade
CALL_TIME_TIMEOUT = 4.0
#: seconds — background operations (recordings, email)
BACKGROUND_TIMEOUT = 30.0


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, ProviderError) and exc.transient


async def with_retries[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    max_backoff: float = 4.0,
) -> T:
    """Run ``operation`` with the standard bounded-retry policy."""
    async for attempt in AsyncRetrying(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=0.25, max=max_backoff),
        reraise=True,
    ):
        with attempt:
            return await operation()
    raise AssertionError("unreachable")  # pragma: no cover
