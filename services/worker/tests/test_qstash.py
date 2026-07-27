"""QStash signature verification and job-route authentication."""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Iterator

import httpx
import pytest
from worker.qstash import QStashVerificationError, verify_qstash_signature

KEY = "sig_current_key"
NEXT_KEY = "sig_next_key"
URL = "https://worker.example.com/jobs/post-call"
BODY = b'{"event": "call.ended", "call_id": "abc"}'


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def mint_token(
    *,
    key: str = KEY,
    url: str = URL,
    body: bytes = BODY,
    exp_offset: int = 300,
    body_claim: str | None = None,
) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    claims = {
        "iss": "Upstash",
        "sub": url,
        "exp": int(time.time()) + exp_offset,
        "body": body_claim if body_claim is not None else _b64url(hashlib.sha256(body).digest()),
    }
    payload = _b64url(json.dumps(claims).encode())
    signature = _b64url(
        hmac.new(key.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{signature}"


def test_valid_signature_passes() -> None:
    verify_qstash_signature(mint_token(), url=URL, body=BODY, current_key=KEY)


def test_next_key_accepted_during_rotation() -> None:
    token = mint_token(key=NEXT_KEY)
    verify_qstash_signature(token, url=URL, body=BODY, current_key=KEY, next_key=NEXT_KEY)


def test_wrong_key_rejected() -> None:
    token = mint_token(key="some_other_key")
    with pytest.raises(QStashVerificationError):
        verify_qstash_signature(token, url=URL, body=BODY, current_key=KEY, next_key=NEXT_KEY)


def test_expired_token_rejected() -> None:
    token = mint_token(exp_offset=-10)
    with pytest.raises(QStashVerificationError, match="expired"):
        verify_qstash_signature(token, url=URL, body=BODY, current_key=KEY)


def test_url_mismatch_rejected() -> None:
    token = mint_token(url="https://attacker.example.com/jobs/post-call")
    with pytest.raises(QStashVerificationError, match="url"):
        verify_qstash_signature(token, url=URL, body=BODY, current_key=KEY)


def test_body_tampering_rejected() -> None:
    token = mint_token()
    with pytest.raises(QStashVerificationError, match="body"):
        verify_qstash_signature(token, url=URL, body=b"tampered", current_key=KEY)


def test_malformed_token_rejected() -> None:
    with pytest.raises(QStashVerificationError):
        verify_qstash_signature("not-a-jwt", url=URL, body=BODY, current_key=KEY)


@pytest.fixture
def app_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[httpx.AsyncClient]:
    monkeypatch.setenv("QSTASH_CURRENT_SIGNING_KEY", KEY)
    monkeypatch.setenv("WORKER_BASE_URL", "https://worker.example.com")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from worker.main import create_app
    from worker.settings import get_settings

    get_settings.cache_clear()
    app = create_app()
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://worker.example.com"
    )
    yield client
    get_settings.cache_clear()


async def test_route_rejects_missing_signature(app_client: httpx.AsyncClient) -> None:
    response = await app_client.post("/jobs/post-call", content=BODY)
    assert response.status_code == 401


async def test_route_rejects_bad_signature(app_client: httpx.AsyncClient) -> None:
    response = await app_client.post(
        "/jobs/post-call",
        content=BODY,
        headers={"Upstash-Signature": mint_token(key="wrong")},
    )
    assert response.status_code == 401


async def test_route_accepts_valid_signature(app_client: httpx.AsyncClient) -> None:
    # Signature passes; the request then fails payload validation (the
    # call_id is not a UUID) — a 422, not a 401, proves auth succeeded.
    response = await app_client.post(
        "/jobs/post-call",
        content=BODY,
        headers={"Upstash-Signature": mint_token()},
    )
    assert response.status_code == 422
