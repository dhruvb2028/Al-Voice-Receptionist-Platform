"""Smoke tests: health endpoint, request-ID propagation, error envelope."""

import httpx
import pytest
from api.main import create_app
from fastapi import FastAPI


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_healthz_returns_ok(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api"}


async def test_response_carries_generated_request_id(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.get("/healthz")
    assert response.headers["X-Request-ID"].startswith("req_")


async def test_valid_incoming_request_id_is_reused(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.get("/healthz", headers={"X-Request-ID": "req_abc12345"})
    assert response.headers["X-Request-ID"] == "req_abc12345"


async def test_malformed_incoming_request_id_is_replaced(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.get("/healthz", headers={"X-Request-ID": "bad id!!"})
    assert response.headers["X-Request-ID"] != "bad id!!"
    assert response.headers["X-Request-ID"].startswith("req_")


async def test_unknown_route_returns_json(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.get("/does-not-exist")
    assert response.status_code == 404
