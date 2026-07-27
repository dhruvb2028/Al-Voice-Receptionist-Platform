"""Streaming LLM provider interface (Groq in production)."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    name: str | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class LLMToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class LLMDelta(BaseModel):
    """One streaming increment: text, a tool call, or the final marker."""

    kind: Literal["text", "tool_call", "done"]
    text: str | None = None
    tool_call: LLMToolCall | None = None


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    first_token_ms: int | None = None
    provider_request_id: str | None = None


class LLMResult(BaseModel):
    text: str
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    cancelled: bool = False


@runtime_checkable
class LLMStream(Protocol):
    def deltas(self) -> AsyncIterator[LLMDelta]: ...

    async def cancel(self) -> None:
        """Stop generation; already-emitted deltas remain valid."""
        ...

    async def result(self) -> LLMResult:
        """Aggregate result with usage and latency; available after the
        stream ends or is cancelled."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    async def stream(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 300,
    ) -> LLMStream: ...


class MockLLMStream:
    def __init__(self, script: "MockTurn") -> None:
        self._script = script
        self._cancelled = False
        self._emitted_text: list[str] = []
        self._emitted_calls: list[LLMToolCall] = []
        self._done = asyncio.Event()

    async def deltas(self) -> AsyncIterator[LLMDelta]:
        for index, word in enumerate(self._script.text.split()):
            if self._cancelled:
                break
            chunk = word if index == 0 else f" {word}"
            self._emitted_text.append(chunk)
            yield LLMDelta(kind="text", text=chunk)
        if not self._cancelled:
            for call in self._script.tool_calls:
                self._emitted_calls.append(call)
                yield LLMDelta(kind="tool_call", tool_call=call)
        self._done.set()
        yield LLMDelta(kind="done")

    async def cancel(self) -> None:
        self._cancelled = True
        self._done.set()

    async def result(self) -> LLMResult:
        text = "".join(self._emitted_text)
        return LLMResult(
            text=text,
            tool_calls=list(self._emitted_calls),
            usage=LLMUsage(
                prompt_tokens=120,
                completion_tokens=max(1, len(text) // 4),
                first_token_ms=42,
                provider_request_id=f"mock-req-{id(self):x}",
            ),
            cancelled=self._cancelled,
        )


class MockTurn(BaseModel):
    text: str = ""
    tool_calls: list[LLMToolCall] = Field(default_factory=list)


class MockLLMProvider:
    """Scripted LLM: returns queued turns in order; repeats the last one."""

    def __init__(self, turns: list[MockTurn] | None = None) -> None:
        self.turns = turns or [MockTurn(text="Hello! How can I help you today?")]
        self.requests: list[list[ChatMessage]] = []
        self._cursor = 0

    def queue(self, turn: MockTurn) -> None:
        self.turns.append(turn)

    def skip(self, count: int) -> None:
        """Advance the script cursor (used when replaying a restored
        session so the next reply continues the script)."""
        self._cursor += count

    async def stream(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 300,
    ) -> MockLLMStream:
        self.requests.append(messages)
        turn = self.turns[min(self._cursor, len(self.turns) - 1)]
        self._cursor += 1
        return MockLLMStream(turn)


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Strict tool-argument parsing: invalid JSON is a provider response
    error, never silently coerced."""
    from ai_providers.errors import ProviderResponseError

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderResponseError("tool arguments were not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ProviderResponseError("tool arguments must be a JSON object")
    return parsed
