"""Token verification tests: missing, malformed, invalid signature,
expired, wrong issuer/audience."""

from collections.abc import Callable

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from tests_markers import requires_db

pytestmark = requires_db


async def _get(client: httpx.AsyncClient, token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await client.get("/me", headers=headers)


async def test_missing_token_rejected(client: httpx.AsyncClient) -> None:
    response = await _get(client)
    assert response.status_code == 401
    body = response.json()["error"]
    assert body["code"] == "unauthorized"
    assert body["request_id"].startswith("req_")


async def test_malformed_token_rejected(client: httpx.AsyncClient) -> None:
    response = await _get(client, "not-a-jwt")
    assert response.status_code == 401


async def test_wrong_signature_rejected(
    client: httpx.AsyncClient,
    mint_token: Callable[..., str],
    wrong_rsa_key: rsa.RSAPrivateKey,
) -> None:
    token = mint_token(platform_role="platform_admin", key=wrong_rsa_key)
    response = await _get(client, token)
    assert response.status_code == 401


async def test_expired_token_rejected(
    client: httpx.AsyncClient, mint_token: Callable[..., str]
) -> None:
    token = mint_token(platform_role="platform_admin", expires_in=-60)
    response = await _get(client, token)
    assert response.status_code == 401


async def test_wrong_issuer_rejected(
    client: httpx.AsyncClient, mint_token: Callable[..., str]
) -> None:
    token = mint_token(platform_role="platform_admin", issuer="https://evil.example")
    response = await _get(client, token)
    assert response.status_code == 401


async def test_wrong_audience_rejected(
    client: httpx.AsyncClient, mint_token: Callable[..., str]
) -> None:
    token = mint_token(platform_role="platform_admin", audience="other-service")
    response = await _get(client, token)
    assert response.status_code == 401


async def test_unknown_kid_rejected(
    client: httpx.AsyncClient, mint_token: Callable[..., str], auth_app: FastAPI
) -> None:
    token = mint_token(platform_role="platform_admin", kid="rotated-away")
    response = await _get(client, token)
    assert response.status_code == 401


async def test_valid_admin_token_accepted(
    client: httpx.AsyncClient, mint_token: Callable[..., str]
) -> None:
    token = mint_token(sub="admin_user", platform_role="platform_admin")
    response = await _get(client, token)
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "platform_admin"
    assert body["tenant_id"] is None
