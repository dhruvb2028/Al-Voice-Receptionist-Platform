"""Groq LLM provider — OpenAI-compatible chat completions with SSE
streaming.

Behavioral contract (same as the mock, verified by contract tests):
streaming text deltas, tool-call deltas, cancellation, first-token
latency, token usage, provider request ID, hard timeout, and retries
that happen ONLY before any user-visible output has begun — once the
caller may have heard something, a failure degrades instead of
retrying (a response must never audibly restart). The final retry
attempt rotates to the configured fallback model.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from ai_providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ai_providers.llm import (
    ChatMessage,
    LLMDelta,
    LLMResult,
    LLMToolCall,
    LLMUsage,
    ToolSpec,
    parse_tool_arguments,
)

logger = structlog.get_logger()

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

#: Safe reply used by the conversation layer when the provider fails
#: after output has begun (no retry permitted).
SAFE_FALLBACK_RESPONSE = (
    "I'm sorry, I'm having a little trouble right now. Let me connect you "
    "with someone who can help."
)


class GroqConfig:
    """Environment-driven model configuration — nothing hardcoded."""

    def __init__(
        self,
        *,
        api_key: str,
        live_model: str,
        fallback_model: str | None = None,
        post_call_model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 300,
        timeout_seconds: float = 8.0,
        max_retries: int = 2,
        base_url: str = GROQ_BASE_URL,
    ) -> None:
        self.api_key = api_key
        self.live_model = live_model
        self.fallback_model = fallback_model
        self.post_call_model = post_call_model or live_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.base_url = base_url.rstrip("/")


def _to_api_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    api_messages: list[dict[str, Any]] = []
    for message in messages:
        entry: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.role == "tool":
            entry["tool_call_id"] = message.tool_call_id
            if message.name:
                entry["name"] = message.name
        api_messages.append(entry)
    return api_messages


def _to_api_tools(tools: list[ToolSpec] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def _map_status_error(status: int, provider: str) -> ProviderError:
    if status in (401, 403):
        return ProviderAuthError(f"authentication failed ({status})", provider=provider)
    if status == 429:
        return ProviderRateLimitError("rate limited", provider=provider)
    if status >= 500:
        return ProviderUnavailableError(f"server error ({status})", provider=provider)
    return ProviderResponseError(f"unexpected status {status}", provider=provider)


class GroqStream:
    """One streaming completion with pre-output retry.

    Attempts rotate models: the live model first, the fallback model on
    the final attempt. A failure after the first visible delta is never
    retried — it propagates so the conversation layer can degrade.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        config: GroqConfig,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
    ) -> None:
        self._client = client
        self._config = config
        self._messages = messages
        self._tools = tools
        self._cancelled = False
        self._text_parts: list[str] = []
        self._tool_calls: list[LLMToolCall] = []
        self._usage = LLMUsage()
        self._started = time.perf_counter()
        self._first_token_at: float | None = None

    def _payload(self, model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": _to_api_messages(self._messages),
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "stream": True,
        }
        api_tools = _to_api_tools(self._tools)
        if api_tools:
            payload["tools"] = api_tools
            payload["tool_choice"] = "auto"
        return payload

    @property
    def output_started(self) -> bool:
        return self._first_token_at is not None

    def _mark_first_token(self) -> None:
        if self._first_token_at is None:
            self._first_token_at = time.perf_counter()
            self._usage.first_token_ms = int((self._first_token_at - self._started) * 1000)

    async def deltas(self) -> AsyncIterator[LLMDelta]:
        attempts = self._config.max_retries + 1
        last_error: ProviderError | None = None
        for attempt in range(attempts):
            model = self._config.live_model
            if attempt == attempts - 1 and self._config.fallback_model:
                model = self._config.fallback_model
                logger.warning("groq_falling_back", model=model)
            try:
                async for delta in self._attempt(model):
                    yield delta
                return
            except ProviderError as exc:
                if self.output_started or not exc.transient:
                    raise
                last_error = exc
                logger.warning(
                    "groq_pre_output_retry",
                    attempt=attempt + 1,
                    model=model,
                    error=str(exc),
                )
        assert last_error is not None
        raise last_error

    async def _attempt(self, model: str) -> AsyncIterator[LLMDelta]:  # noqa: C901
        pending_tools: dict[int, dict[str, Any]] = {}
        try:
            async with asyncio.timeout(self._config.timeout_seconds):
                async with self._client.stream(
                    "POST", "/chat/completions", json=self._payload(model)
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise _map_status_error(response.status_code, "groq")
                    self._usage.provider_request_id = response.headers.get("x-request-id")

                    async for line in response.aiter_lines():
                        if self._cancelled:
                            break
                        if not line.startswith("data: "):
                            continue
                        data = line[len("data: ") :].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError as exc:
                            raise ProviderResponseError(
                                "malformed stream event", provider="groq"
                            ) from exc

                        usage = event.get("x_groq", {}).get("usage") or event.get("usage")
                        if usage:
                            self._usage.prompt_tokens = usage.get("prompt_tokens", 0)
                            self._usage.completion_tokens = usage.get("completion_tokens", 0)

                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}

                        content = delta.get("content")
                        if content:
                            self._mark_first_token()
                            self._text_parts.append(content)
                            yield LLMDelta(kind="text", text=content)

                        for tool_delta in delta.get("tool_calls") or []:
                            index = tool_delta.get("index", 0)
                            slot = pending_tools.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            if tool_delta.get("id"):
                                slot["id"] = tool_delta["id"]
                            function = tool_delta.get("function") or {}
                            if function.get("name"):
                                slot["name"] = function["name"]
                            if function.get("arguments"):
                                slot["arguments"] += function["arguments"]
        except TimeoutError as exc:
            raise ProviderTimeoutError("completion timed out", provider="groq") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(str(exc), provider="groq") from exc

        # Completed tool calls surface after the stream closes; arguments
        # are parsed strictly — malformed JSON fails closed.
        for slot in pending_tools.values():
            if not slot["name"]:
                continue
            call = LLMToolCall(
                id=slot["id"] or f"call_{len(self._tool_calls)}",
                name=slot["name"],
                arguments=parse_tool_arguments(slot["arguments"] or "{}"),
            )
            self._mark_first_token()
            self._tool_calls.append(call)
            yield LLMDelta(kind="tool_call", tool_call=call)

        yield LLMDelta(kind="done")

    async def cancel(self) -> None:
        self._cancelled = True

    async def result(self) -> LLMResult:
        return LLMResult(
            text="".join(self._text_parts),
            tool_calls=list(self._tool_calls),
            usage=self._usage,
            cancelled=self._cancelled,
        )


class GroqLLMProvider:
    """LLMProvider implementation for live turns."""

    def __init__(self, config: GroqConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
        )

    async def stream(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 300,
    ) -> GroqStream:
        # temperature/max_tokens come from environment configuration; the
        # signature keeps LLMProvider protocol compatibility.
        return GroqStream(
            client=self._client,
            config=self._config,
            messages=messages,
            tools=tools,
        )

    async def close(self) -> None:
        await self._client.aclose()
