"""Shared test markers: skip database-backed tests when unreachable."""

import os
import socket

import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:test@localhost:55432/receptionist_test",
)


def _db_reachable() -> bool:
    """Cheap TCP probe; full connectivity is exercised by the fixtures."""
    from sqlalchemy.engine import make_url

    url = make_url(TEST_DATABASE_URL)
    try:
        with socket.create_connection((url.host or "localhost", url.port or 5432), timeout=2):
            return True
    except OSError:
        return False


DB_AVAILABLE = _db_reachable()

requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="test database not reachable")
