"""Smoke tests for the voice service bootstrap."""

import httpx
import pytest
from fastapi import FastAPI
from voice.main import create_app


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
    assert response.json() == {"status": "ok", "service": "voice"}


async def test_request_id_header_present(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.get("/healthz")
    assert response.headers["X-Request-ID"].startswith("req_")
