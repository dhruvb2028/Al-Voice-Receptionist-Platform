"""Async engine and session factory.

Services get sessions only through these factories so pool sizing,
timeouts, and (later) the per-transaction ``app.tenant_id`` RLS setting
are applied uniformly.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str, *, pool_size: int = 5, echo: bool = False) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=echo,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
