"""Cloudflare R2 storage provider (S3-compatible).

Implements the StorageProvider contract. boto3's S3 client handles
SigV4; calls run in a worker thread so the async paths never block.
Objects are tenant-prefixed, never public — access is exclusively via
short-lived presigned URLs minted after authorization.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from ai_providers.errors import ProviderResponseError, ProviderUnavailableError
from ai_providers.storage import SignedUrl, StoredObject

logger = structlog.get_logger()


class R2StorageProvider:
    def __init__(
        self,
        *,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        endpoint: str | None = None,
    ) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint or f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
            region_name="auto",
        )

    async def upload(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        retain_days: int | None = None,
    ) -> StoredObject:
        retain_until = datetime.now(UTC) + timedelta(days=retain_days) if retain_days else None
        metadata = {"retain-until": retain_until.isoformat()} if retain_until else {}

        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata=metadata,
            )

        try:
            await asyncio.to_thread(_put)
        except Exception as exc:
            raise ProviderUnavailableError(f"r2 upload failed: {exc}", provider="r2") from exc
        return StoredObject(
            key=key,
            size_bytes=len(data),
            content_type=content_type,
            retain_until=retain_until,
        )

    async def signed_url(self, *, key: str, expires_seconds: int = 900) -> SignedUrl:
        def _sign() -> str:
            return self._client.generate_presigned_url(  # type: ignore[no-any-return]
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )

        try:
            url = await asyncio.to_thread(_sign)
        except Exception as exc:
            raise ProviderResponseError(f"r2 signing failed: {exc}", provider="r2") from exc
        return SignedUrl(
            url=url,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_seconds),
        )

    async def delete(self, *, key: str) -> bool:
        existed = await self.head(key=key) is not None

        def _delete() -> None:
            self._client.delete_object(Bucket=self._bucket, Key=key)

        try:
            await asyncio.to_thread(_delete)
        except Exception as exc:
            raise ProviderUnavailableError(f"r2 delete failed: {exc}", provider="r2") from exc
        return existed

    async def head(self, *, key: str) -> StoredObject | None:
        def _head() -> dict[str, Any] | None:
            try:
                return self._client.head_object(Bucket=self._bucket, Key=key)  # type: ignore[no-any-return]
            except self._client.exceptions.ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                    return None
                raise

        try:
            response = await asyncio.to_thread(_head)
        except Exception as exc:
            raise ProviderUnavailableError(f"r2 head failed: {exc}", provider="r2") from exc
        if response is None:
            return None
        retain_raw = response.get("Metadata", {}).get("retain-until")
        return StoredObject(
            key=key,
            size_bytes=int(response.get("ContentLength", 0)),
            content_type=response.get("ContentType", "application/octet-stream"),
            retain_until=datetime.fromisoformat(retain_raw) if retain_raw else None,
        )
