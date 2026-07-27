"""Cache provider interface (Upstash Redis in production).

Key discipline: every tenant-scoped key is prefixed
``tenant:{tenant_id}:`` by this layer — callers pass logical names only,
so a caller cannot accidentally read another tenant's entry.
"""

import time
import uuid
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CacheProvider(Protocol):
    async def get_tenant_config(self, *, tenant_id: uuid.UUID) -> dict[str, Any] | None: ...

    async def set_tenant_config(
        self, *, tenant_id: uuid.UUID, config: dict[str, Any], ttl_seconds: int = 300
    ) -> None: ...

    async def set_call_state(
        self, *, call_id: str, state: dict[str, Any], ttl_seconds: int = 7200
    ) -> None: ...

    async def get_call_state(self, *, call_id: str) -> dict[str, Any] | None: ...

    async def acquire_lock(self, *, name: str, ttl_seconds: int = 30) -> str | None:
        """Returns a lock token, or None when already held."""
        ...

    async def release_lock(self, *, name: str, token: str) -> bool:
        """Releases only when the token matches (no stealing)."""
        ...

    async def check_rate_limit(self, *, key: str, limit: int, window_seconds: int) -> bool:
        """True when the action is allowed; False when the window's
        budget is exhausted."""
        ...


class MockCacheProvider:
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float | None]] = {}
        self._locks: dict[str, str] = {}
        self._counters: dict[str, list[float]] = {}

    def _get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires is not None and expires < time.monotonic():
            del self._store[key]
            return None
        return value

    def _set(self, key: str, value: Any, ttl_seconds: int | None) -> None:
        expires = time.monotonic() + ttl_seconds if ttl_seconds else None
        self._store[key] = (value, expires)

    async def get_tenant_config(self, *, tenant_id: uuid.UUID) -> dict[str, Any] | None:
        value = self._get(f"tenant:{tenant_id}:config")
        return dict(value) if value is not None else None

    async def set_tenant_config(
        self, *, tenant_id: uuid.UUID, config: dict[str, Any], ttl_seconds: int = 300
    ) -> None:
        self._set(f"tenant:{tenant_id}:config", dict(config), ttl_seconds)

    async def set_call_state(
        self, *, call_id: str, state: dict[str, Any], ttl_seconds: int = 7200
    ) -> None:
        self._set(f"call:{call_id}:state", dict(state), ttl_seconds)

    async def get_call_state(self, *, call_id: str) -> dict[str, Any] | None:
        value = self._get(f"call:{call_id}:state")
        return dict(value) if value is not None else None

    async def acquire_lock(self, *, name: str, ttl_seconds: int = 30) -> str | None:
        if name in self._locks:
            return None
        token = uuid.uuid4().hex
        self._locks[name] = token
        return token

    async def release_lock(self, *, name: str, token: str) -> bool:
        if self._locks.get(name) == token:
            del self._locks[name]
            return True
        return False

    async def check_rate_limit(self, *, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        window = self._counters.setdefault(key, [])
        window[:] = [stamp for stamp in window if stamp > now - window_seconds]
        if len(window) >= limit:
            return False
        window.append(now)
        return True
