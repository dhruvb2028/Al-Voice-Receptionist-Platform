"""Alembic environment. Async engine, offline + online modes.

The URL comes from DATABASE_DIRECT_URL (unpooled, for DDL) with a
fallback to DATABASE_URL. Autogenerate targets the shared metadata in
ai_database.metadata.
"""

import asyncio
import os

import ai_database.models  # noqa: F401  — registers all tables on the metadata
from ai_database.metadata import Base
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_DIRECT_URL (or DATABASE_URL) must be set to run migrations.")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_sync_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
