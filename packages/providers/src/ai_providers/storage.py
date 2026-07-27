"""Object-storage provider interface (Cloudflare R2 in production)."""

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class StoredObject(BaseModel):
    key: str
    size_bytes: int
    content_type: str
    #: retention deadline recorded with the object; the sweeper deletes
    #: objects past it
    retain_until: datetime | None = None


class SignedUrl(BaseModel):
    url: str
    expires_at: datetime


@runtime_checkable
class StorageProvider(Protocol):
    async def upload(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        retain_days: int | None = None,
    ) -> StoredObject: ...

    async def signed_url(self, *, key: str, expires_seconds: int = 900) -> SignedUrl:
        """Short-lived read URL; issued only after the API authorizes the
        requester for this specific object."""
        ...

    async def delete(self, *, key: str) -> bool:
        """Idempotent delete; returns whether the object existed."""
        ...

    async def head(self, *, key: str) -> StoredObject | None: ...


class MockStorageProvider:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[StoredObject, bytes]] = {}
        self.deleted_keys: list[str] = []

    async def upload(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        retain_days: int | None = None,
    ) -> StoredObject:
        retain_until = datetime.now(UTC) + timedelta(days=retain_days) if retain_days else None
        obj = StoredObject(
            key=key,
            size_bytes=len(data),
            content_type=content_type,
            retain_until=retain_until,
        )
        self.objects[key] = (obj, data)
        return obj

    async def signed_url(self, *, key: str, expires_seconds: int = 900) -> SignedUrl:
        from ai_providers.errors import ProviderResponseError

        if key not in self.objects:
            raise ProviderResponseError(f"object '{key}' does not exist")
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_seconds)
        return SignedUrl(
            url=f"https://mock-storage.invalid/{key}?exp={int(expires_at.timestamp())}",
            expires_at=expires_at,
        )

    async def delete(self, *, key: str) -> bool:
        existed = key in self.objects
        self.objects.pop(key, None)
        self.deleted_keys.append(key)
        return existed

    async def head(self, *, key: str) -> StoredObject | None:
        entry = self.objects.get(key)
        return entry[0] if entry else None
