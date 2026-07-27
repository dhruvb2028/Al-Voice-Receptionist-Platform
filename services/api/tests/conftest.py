"""API test fixtures: RSA-signed Clerk-style tokens and a database-backed
app instance (skipped when the test database is unreachable)."""

import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from jwt import PyJWK
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ISSUER = "https://clerk.test.example"
AUDIENCE = "receptionist-api"
KID = "test-key-1"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:test@localhost:55432/receptionist_test",
)


# --- RSA keypair + token minting -------------------------------------------


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def wrong_rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def mint_token(rsa_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def _mint(
        *,
        sub: str = "user_test",
        org_id: str | None = None,
        org_role: str | None = None,
        platform_role: str | None = None,
        issuer: str = ISSUER,
        audience: str = AUDIENCE,
        expires_in: int = 300,
        key: rsa.RSAPrivateKey | None = None,
        kid: str = KID,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": sub,
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + expires_in,
        }
        if org_id:
            payload["org_id"] = org_id
        if org_role:
            payload["org_role"] = org_role
        if platform_role:
            payload["platform_role"] = platform_role
        signing_key = key or rsa_key
        pem = signing_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})

    return _mint


def _prime_verifier(rsa_key: rsa.RSAPrivateKey) -> None:
    """Configure the app's token verifier with the test public key."""
    from api.auth import dependencies as deps
    from api.auth.verify import JwksCache, TokenVerifier

    public = rsa_key.public_key()
    jwk_dict = jwt.algorithms.RSAAlgorithm.to_jwk(public, as_dict=True)
    jwk_dict["kid"] = KID
    jwk_dict["alg"] = "RS256"
    cache = JwksCache("https://unused.example/jwks.json")
    cache.prime({KID: PyJWK(jwk_dict)})
    deps._verifier = TokenVerifier(jwks=cache, issuer=ISSUER, audience=AUDIENCE)


# --- Database availability --------------------------------------------------

from tests_markers import DB_AVAILABLE  # noqa: E402


@pytest.fixture(scope="session")
def migrated_database() -> str:
    if not DB_AVAILABLE:
        pytest.skip("test database not reachable")
    from alembic import command
    from alembic.config import Config

    os.environ["DATABASE_DIRECT_URL"] = TEST_DATABASE_URL
    command.upgrade(Config("alembic.ini"), "head")
    return TEST_DATABASE_URL


# --- App wired to the test database ----------------------------------------


@pytest.fixture
async def auth_app(migrated_database: str, rsa_key: rsa.RSAPrivateKey) -> AsyncIterator[FastAPI]:
    """App with real auth chain and a test-database session dependency."""
    from api.db import get_session
    from api.main import create_app
    from api.settings import get_settings

    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = migrated_database
    app = create_app()
    _prime_verifier(rsa_key)

    engine = create_async_engine(migrated_database)

    async def _test_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = _test_session
    try:
        yield app
    finally:
        from api.auth.dependencies import reset_verifier

        reset_verifier()
        get_settings.cache_clear()
        await engine.dispose()


@pytest.fixture
def client(auth_app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=auth_app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# --- Seeded tenant data ------------------------------------------------------


@pytest.fixture
async def seeded_tenants(migrated_database: str) -> AsyncIterator[dict[str, Any]]:
    """Two tenants with members in various states; cleaned up after."""
    from ai_database.enums import MemberRole, MemberStatus, TenantStatus
    from ai_database.models import Call, Tenant, TenantMember

    engine = create_async_engine(migrated_database)
    suffix = uuid.uuid4().hex[:8]
    data: dict[str, Any] = {"suffix": suffix}

    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        tenant_a = Tenant(
            name="Auth Tenant A",
            slug=f"auth-a-{suffix}",
            external_auth_org_id=f"org_a_{suffix}",
            status=TenantStatus.ACTIVE,
        )
        tenant_b = Tenant(
            name="Auth Tenant B",
            slug=f"auth-b-{suffix}",
            external_auth_org_id=f"org_b_{suffix}",
            status=TenantStatus.ACTIVE,
        )
        suspended = Tenant(
            name="Suspended Tenant",
            slug=f"auth-s-{suffix}",
            external_auth_org_id=f"org_s_{suffix}",
            status=TenantStatus.SUSPENDED,
        )
        session.add_all([tenant_a, tenant_b, suspended])
        await session.flush()

        members = [
            TenantMember(
                tenant_id=tenant_a.id,
                external_user_id=f"owner_a_{suffix}",
                role=MemberRole.CLIENT_OWNER,
                status=MemberStatus.ACTIVE,
            ),
            TenantMember(
                tenant_id=tenant_a.id,
                external_user_id=f"staff_a_{suffix}",
                role=MemberRole.CLIENT_STAFF,
                status=MemberStatus.ACTIVE,
            ),
            TenantMember(
                tenant_id=tenant_a.id,
                external_user_id=f"inactive_a_{suffix}",
                role=MemberRole.CLIENT_STAFF,
                status=MemberStatus.DISABLED,
            ),
            TenantMember(
                tenant_id=suspended.id,
                external_user_id=f"owner_s_{suffix}",
                role=MemberRole.CLIENT_OWNER,
                status=MemberStatus.ACTIVE,
            ),
        ]
        session.add_all(members)

        from datetime import UTC, datetime

        call_b = Call(
            tenant_id=tenant_b.id,
            provider_call_sid=f"CA_auth_{suffix}",
            to_number="+15555550199",
            started_at=datetime.now(UTC),
        )
        session.add(call_b)
        await session.flush()

        data.update(
            tenant_a_id=tenant_a.id,
            tenant_b_id=tenant_b.id,
            suspended_id=suspended.id,
            org_a=tenant_a.external_auth_org_id,
            org_b=tenant_b.external_auth_org_id,
            org_s=suspended.external_auth_org_id,
            owner_a=f"owner_a_{suffix}",
            staff_a=f"staff_a_{suffix}",
            inactive_a=f"inactive_a_{suffix}",
            owner_s=f"owner_s_{suffix}",
            call_b_id=call_b.id,
        )

    try:
        yield data
    finally:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                text("DELETE FROM calls WHERE provider_call_sid = :sid"),
                {"sid": f"CA_auth_{suffix}"},
            )
            await session.execute(
                text("DELETE FROM tenants WHERE slug LIKE :pat"), {"pat": f"auth-%-{suffix}"}
            )
        await engine.dispose()
