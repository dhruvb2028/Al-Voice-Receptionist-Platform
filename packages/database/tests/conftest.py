"""Database test fixtures.

Requires a running PostgreSQL (local: docker postgres:16-alpine on
:55432; CI: a postgres service container). The suite skips cleanly when
TEST_DATABASE_URL is unreachable so unit-only runs stay green.

Each test runs inside a transaction rolled back at teardown, on a schema
migrated once per session via Alembic — the same migration path
production uses.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:test@localhost:55432/receptionist_test",
)


def _database_reachable() -> bool:
    async def probe() -> bool:
        try:
            engine = create_async_engine(TEST_DATABASE_URL, connect_args={"timeout": 3})
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
        except Exception:
            return False
        return True

    return asyncio.new_event_loop().run_until_complete(probe())


DB_AVAILABLE = _database_reachable()

pytestmark = pytest.mark.skipif(not DB_AVAILABLE, reason="test database not reachable")


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Upgrade the test database to head once per session."""
    if not DB_AVAILABLE:
        pytest.skip("test database not reachable")
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    os.environ["DATABASE_DIRECT_URL"] = TEST_DATABASE_URL
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def db_session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    """A session inside a transaction that always rolls back."""
    engine = create_async_engine(migrated_database)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


@pytest.fixture
async def two_tenants(db_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Two tenants for isolation tests."""
    from ai_database.models import Tenant

    a = Tenant(name="Tenant A", slug=f"tenant-a-{uuid.uuid4().hex[:8]}")
    b = Tenant(name="Tenant B", slug=f"tenant-b-{uuid.uuid4().hex[:8]}")
    db_session.add_all([a, b])
    await db_session.flush()
    return a.id, b.id
