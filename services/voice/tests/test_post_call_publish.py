"""Voice-side QStash publisher: exactly-one-job semantics via
deduplication id, best-effort failure handling."""

import uuid

import httpx
from voice.post_call_publish import PostCallPublisher


def _publisher(handler: httpx.MockTransport) -> PostCallPublisher:
    return PostCallPublisher(
        qstash_token="qs_test_token",
        qstash_url="https://qstash.example",
        worker_base_url="https://worker.example.com/",
        client=httpx.AsyncClient(
            transport=handler, headers={"Authorization": "Bearer qs_test_token"}
        ),
    )


async def test_publish_targets_worker_with_dedup_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messageId": "msg_1"})

    call_id = uuid.uuid4()
    publisher = _publisher(httpx.MockTransport(handler))
    assert await publisher.publish_call_ended(call_id) is True

    request = seen[0]
    assert (
        str(request.url)
        == "https://qstash.example/v2/publish/https://worker.example.com/jobs/post-call"
    )
    assert request.headers["Upstash-Deduplication-Id"] == f"post-call-{call_id}"
    assert request.headers["Upstash-Retries"] == "3"
    assert f'"{call_id}"' in request.content.decode()


async def test_publish_failure_is_swallowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    publisher = _publisher(httpx.MockTransport(handler))
    assert await publisher.publish_call_ended(uuid.uuid4()) is False
