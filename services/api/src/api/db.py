"""Database session dependency for the API service."""

from collections.abc import AsyncIterator

from ai_database import create_engine, create_session_factory
from ai_shared.errors import PlatformError
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api.settings import get_settings


class DatabaseNotConfiguredError(PlatformError):
    code = "database_not_configured"
    status_code = 503


_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def _get_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _factory
    if _factory is None:
        settings = get_settings()
        if not settings.database_url:
            raise DatabaseNotConfiguredError("Database is not configured.")
        _engine = create_engine(settings.database_url)
        _factory = create_session_factory(_engine)
    return _factory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request; commits on success, rolls back on error."""
    factory = _get_factory()
    async with factory() as session, session.begin():
        yield session


async def dispose_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None
