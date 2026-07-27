"""Database access for the worker service (job-scoped sessions)."""

from ai_database import create_engine, create_session_factory
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from worker.settings import get_settings

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """None when no database is configured (unit tests)."""
    global _engine, _factory
    if _factory is None:
        settings = get_settings()
        if not settings.database_url:
            return None
        _engine = create_engine(settings.database_url)
        _factory = create_session_factory(_engine)
    return _factory


async def dispose_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None
