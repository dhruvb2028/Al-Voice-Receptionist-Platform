"""Publish ``call.ended`` to QStash when a call reaches a terminal state.

Exactly one job per call: the QStash deduplication id is derived from
the call id, so re-publishing (crash between finalize and ack, retried
teardown) never enqueues a second job. Publishing is best-effort at the
call site — a failure is logged and the retention sweep of
``post_processing_status = 'pending'`` calls backstops it.
"""

import uuid
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

#: bounded retries then dead-letter, handled by QStash itself
QSTASH_RETRIES = 3


class PostCallPublisher:
    def __init__(
        self,
        *,
        qstash_token: str,
        qstash_url: str,
        worker_base_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._destination = f"{worker_base_url.rstrip('/')}/jobs/post-call"
        self._publish_url = f"{qstash_url.rstrip('/')}/v2/publish/{self._destination}"
        self._client = client or httpx.AsyncClient(
            headers={"Authorization": f"Bearer {qstash_token}"}, timeout=5.0
        )

    async def publish_call_ended(self, call_id: uuid.UUID) -> bool:
        """True when the job was accepted. Never raises."""
        payload: dict[str, Any] = {"event": "call.ended", "call_id": str(call_id)}
        try:
            response = await self._client.post(
                self._publish_url,
                json=payload,
                headers={
                    "Upstash-Deduplication-Id": f"post-call-{call_id}",
                    "Upstash-Retries": str(QSTASH_RETRIES),
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("post_call_publish_failed", call_id=str(call_id), error=str(exc))
            return False
        logger.info("post_call_published", call_id=str(call_id))
        return True

    async def aclose(self) -> None:
        await self._client.aclose()


def build_publisher() -> PostCallPublisher | None:
    """None when QStash is not configured (local/simulator runs)."""
    from voice.settings import get_settings

    settings = get_settings()
    if not (settings.qstash_token and settings.worker_base_url):
        return None
    return PostCallPublisher(
        qstash_token=settings.qstash_token,
        qstash_url=settings.qstash_url,
        worker_base_url=settings.worker_base_url,
    )
