"""Transport security: headers, body limits, rate limiting, and secret
resolution."""

import httpx
import pytest
from ai_shared.errors import NotFoundError
from ai_shared.fastapi_setup import configure_service_app
from ai_shared.secrets import SecretResolver
from ai_shared.security import (
    SECURITY_HEADERS,
    InMemoryRateLimiter,
    client_key,
)
from fastapi import FastAPI, Request


def _app(**kwargs: object) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/echo")
    async def echo(payload: dict[str, str]) -> dict[str, str]:
        return payload

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise NotFoundError("Not found.")

    return configure_service_app(app, service_name="test", **kwargs)  # type: ignore[arg-type]


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test.example"
    )


# --- headers -----------------------------------------------------------------


async def test_security_headers_on_success() -> None:
    async with _client(_app()) as client:
        response = await client.get("/ping")
    assert response.status_code == 200
    for header, value in SECURITY_HEADERS.items():
        assert response.headers[header] == value


async def test_security_headers_on_error_responses() -> None:
    """An error path must not lose its headers."""
    async with _client(_app()) as client:
        response = await client.get("/boom")
    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_request_id_present_on_rate_limited_response() -> None:
    """Ordering check: the security layer runs inside the request-id
    layer, so a rejected request is still traceable."""
    app = _app(rate_limit=1)
    async with _client(app) as client:
        await client.get("/ping")
        response = await client.get("/ping")
    assert response.status_code == 429
    assert response.headers.get("X-Request-ID")


# --- body limits -------------------------------------------------------------


async def test_oversized_body_is_refused() -> None:
    app = _app(max_body_bytes=100)
    async with _client(app) as client:
        response = await client.post("/echo", json={"a": "x" * 500})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


async def test_body_within_the_limit_is_accepted() -> None:
    app = _app(max_body_bytes=10_000)
    async with _client(app) as client:
        response = await client.post("/echo", json={"a": "ok"})
    assert response.status_code == 200


async def test_malformed_content_length_is_refused() -> None:
    app = _app()
    async with _client(app) as client:
        response = await client.post(
            "/echo", content=b"{}", headers={"content-length": "not-a-number"}
        )
    assert response.status_code == 400


# --- rate limiting -----------------------------------------------------------


async def test_rate_limit_blocks_after_the_budget() -> None:
    app = _app(rate_limit=3, rate_limit_window_seconds=60)
    async with _client(app) as client:
        codes = [(await client.get("/ping")).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]


async def test_rate_limited_response_advertises_retry_after() -> None:
    app = _app(rate_limit=1, rate_limit_window_seconds=30)
    async with _client(app) as client:
        await client.get("/ping")
        response = await client.get("/ping")
    assert response.headers["Retry-After"] == "30"


async def test_rate_limit_is_per_caller() -> None:
    """One noisy caller must not exhaust another's budget."""
    app = _app(rate_limit=2)
    async with _client(app) as client:
        noisy = {"x-forwarded-for": "203.0.113.10"}
        quiet = {"x-forwarded-for": "203.0.113.99"}
        for _ in range(3):
            await client.get("/ping", headers=noisy)
        blocked = await client.get("/ping", headers=noisy)
        other = await client.get("/ping", headers=quiet)
    assert blocked.status_code == 429
    assert other.status_code == 200


async def test_limiter_window_rolls_over() -> None:
    now = [1000.0]
    limiter = InMemoryRateLimiter(_clock=lambda: now[0])
    assert await limiter.allow(key="k", limit=1, window_seconds=60) is True
    assert await limiter.allow(key="k", limit=1, window_seconds=60) is False
    now[0] += 61
    assert await limiter.allow(key="k", limit=1, window_seconds=60) is True


async def test_limiter_prunes_expired_windows() -> None:
    now = [0.0]
    limiter = InMemoryRateLimiter(_clock=lambda: now[0])
    for step in range(5):
        now[0] = step * 60.0
        await limiter.allow(key=f"caller-{step}", limit=10, window_seconds=60)
    # Only the current window's state survives.
    assert len(limiter._counts) == 1


async def test_zero_limit_denies_everything() -> None:
    limiter = InMemoryRateLimiter()
    assert await limiter.allow(key="k", limit=0, window_seconds=60) is False


def test_client_key_prefers_the_authenticated_principal() -> None:
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.5")],
        "client": ("10.0.0.1", 1234),
    }
    request = Request(scope)
    assert client_key(request).startswith("ip:203.0.113.5")
    request.state.principal_key = "tenant-a:user-1"
    assert client_key(request) == "principal:tenant-a:user-1"


# --- secrets -----------------------------------------------------------------


class _Backend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def access(self, resource_name: str) -> str:
        self.calls.append(resource_name)
        return "resolved-secret"


def test_literal_values_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_KEY", "literal-value")
    resolver = SecretResolver(backend=_Backend())
    assert resolver.resolve("SOME_KEY") == "literal-value"


def test_secret_reference_is_dereferenced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_KEY", "sm://projects/p/secrets/s/versions/latest")
    backend = _Backend()
    resolver = SecretResolver(backend=backend)
    assert resolver.resolve("SOME_KEY") == "resolved-secret"
    assert backend.calls == ["projects/p/secrets/s/versions/latest"]


def test_resolved_secrets_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_KEY", "sm://projects/p/secrets/s/versions/1")
    backend = _Backend()
    resolver = SecretResolver(backend=backend)
    for _ in range(3):
        resolver.resolve("SOME_KEY")
    assert len(backend.calls) == 1


def test_missing_value_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ABSENT_KEY", raising=False)
    resolver = SecretResolver(backend=_Backend())
    assert resolver.resolve("ABSENT_KEY") is None
    assert resolver.resolve("ABSENT_KEY", "fallback") == "fallback"


def test_repr_never_exposes_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_KEY", "sm://projects/p/secrets/s/versions/1")
    resolver = SecretResolver(backend=_Backend())
    resolver.resolve("SOME_KEY")
    assert "resolved-secret" not in repr(resolver)
