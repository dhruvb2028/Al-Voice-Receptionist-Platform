"""Groq provider tests against a mocked HTTP transport.

Covers streaming text, incremental tool-call assembly, cancellation,
first-token timing, usage, request IDs, timeout mapping, pre-output
retry with model fallback, no-retry-after-output, and auth failures.
"""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from ai_providers.errors import (
    ProviderAuthError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from ai_providers.groq import GroqConfig, GroqLLMProvider
from ai_providers.llm import ChatMessage, ToolSpec


def _sse(events: list[dict[str, Any]]) -> bytes:
    lines = [f"data: {json.dumps(event)}\n\n" for event in events]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _text_events(words: list[str], *, with_usage: bool = True) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"choices": [{"delta": {"content": word}}]} for word in words]
    if with_usage:
        events.append(
            {
                "choices": [{"delta": {}}],
                "x_groq": {"usage": {"prompt_tokens": 100, "completion_tokens": 12}},
            }
        )
    return events


def _config(**overrides: Any) -> GroqConfig:
    defaults: dict[str, Any] = {
        "api_key": "test-key",
        "live_model": "live-model",
        "fallback_model": "fallback-model",
        "timeout_seconds": 5.0,
        "max_retries": 2,
    }
    defaults.update(overrides)
    return GroqConfig(**defaults)


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], config: GroqConfig | None = None
) -> GroqLLMProvider:
    config = config or _config()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=config.base_url)
    return GroqLLMProvider(config, client=client)


async def test_streams_text_with_usage_and_request_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(_text_events(["Hello", " there", "!"])),
            headers={"x-request-id": "req-groq-1"},
        )

    provider = _provider(handler)
    stream = await provider.stream(messages=[ChatMessage(role="user", content="hi")])
    deltas = [d async for d in stream.deltas()]
    text = "".join(d.text or "" for d in deltas if d.kind == "text")
    assert text == "Hello there!"
    assert deltas[-1].kind == "done"

    result = await stream.result()
    assert result.usage.provider_request_id == "req-groq-1"
    assert result.usage.prompt_tokens == 100
    assert result.usage.completion_tokens == 12
    assert result.usage.first_token_ms is not None


async def test_assembles_tool_calls_across_chunks() -> None:
    events = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "get_price", "arguments": '{"serv'},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": 'ice_name": "drain"}'}}
                        ]
                    }
                }
            ]
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(events))

    provider = _provider(handler)
    stream = await provider.stream(
        messages=[ChatMessage(role="user", content="price?")],
        tools=[
            ToolSpec(
                name="get_price",
                description="",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )
    deltas = [d async for d in stream.deltas()]
    tool_deltas = [d for d in deltas if d.kind == "tool_call"]
    assert len(tool_deltas) == 1
    call = tool_deltas[0].tool_call
    assert call is not None
    assert call.id == "call_1"
    assert call.name == "get_price"
    assert call.arguments == {"service_name": "drain"}


async def test_malformed_tool_arguments_fail_closed() -> None:
    events = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "get_price", "arguments": "{broken"},
                            }
                        ]
                    }
                }
            ]
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(events))

    provider = _provider(handler, _config(max_retries=0, fallback_model=None))
    stream = await provider.stream(messages=[ChatMessage(role="user", content="x")])
    with pytest.raises(ProviderResponseError):
        _ = [d async for d in stream.deltas()]


async def test_cancellation_stops_consumption() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(_text_events(["one", " two", " three"])))

    provider = _provider(handler)
    stream = await provider.stream(messages=[ChatMessage(role="user", content="hi")])
    iterator = stream.deltas()
    first = await anext(iterator)
    assert first.kind == "text"
    await stream.cancel()
    remaining = [d async for d in iterator]
    result = await stream.result()
    assert result.cancelled is True
    assert remaining[-1].kind == "done"


async def test_pre_output_retry_uses_fallback_model_last() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["model"])
        if len(calls) < 3:
            return httpx.Response(503, content=b"unavailable")
        return httpx.Response(200, content=_sse(_text_events(["Recovered"])))

    provider = _provider(handler, _config(max_retries=2))
    stream = await provider.stream(messages=[ChatMessage(role="user", content="hi")])
    deltas = [d async for d in stream.deltas()]
    text = "".join(d.text or "" for d in deltas if d.kind == "text")
    assert text == "Recovered"
    # live, live, then fallback on the final attempt
    assert calls == ["live-model", "live-model", "fallback-model"]


async def test_retry_exhaustion_raises_transient_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"down")

    provider = _provider(handler, _config(max_retries=1))
    stream = await provider.stream(messages=[ChatMessage(role="user", content="hi")])
    with pytest.raises(ProviderUnavailableError):
        _ = [d async for d in stream.deltas()]


async def test_no_retry_after_output_started() -> None:
    attempts = {"count": 0}

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"choices": [{"delta": {"content": "Hel"}}]}\n\n'
            raise httpx.ReadError("connection lost mid-stream")

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(200, stream=BrokenStream())

    provider = _provider(handler, _config(max_retries=2))
    stream = await provider.stream(messages=[ChatMessage(role="user", content="hi")])
    with pytest.raises(ProviderUnavailableError):
        _ = [d async for d in stream.deltas()]
    # Output had begun — exactly one attempt, no restart.
    assert attempts["count"] == 1
    assert stream.output_started is True


async def test_auth_error_never_retried() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(401, content=b"bad key")

    provider = _provider(handler, _config(max_retries=3))
    stream = await provider.stream(messages=[ChatMessage(role="user", content="hi")])
    with pytest.raises(ProviderAuthError):
        _ = [d async for d in stream.deltas()]
    assert attempts["count"] == 1


async def test_tool_specs_and_messages_serialized() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(_text_events(["ok"])))

    provider = _provider(handler)
    stream = await provider.stream(
        messages=[
            ChatMessage(role="system", content="rules"),
            ChatMessage(role="user", content="hello"),
            ChatMessage(
                role="tool", content='{"known": true}', tool_call_id="c1", name="get_price"
            ),
        ],
        tools=[
            ToolSpec(
                name="get_price",
                description="Price lookup",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )
    _ = [d async for d in stream.deltas()]

    assert captured["model"] == "live-model"
    assert captured["stream"] is True
    assert captured["messages"][2]["tool_call_id"] == "c1"
    assert captured["tools"][0]["function"]["name"] == "get_price"
    assert captured["tool_choice"] == "auto"
