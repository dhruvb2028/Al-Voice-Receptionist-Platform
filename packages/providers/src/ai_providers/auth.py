"""Authentication provider interface (organizations + invitations).

The API service manages Clerk organizations when creating tenants. The
interface keeps Clerk swappable and gives tests/local development a
no-network implementation.
"""

from typing import Protocol

import httpx
import structlog
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = structlog.get_logger()


class ProviderError(Exception):
    """A provider call failed after bounded retries."""


class CreatedOrganization(BaseModel):
    external_org_id: str


class AuthenticationProvider(Protocol):
    """Organization and invitation management."""

    async def create_organization(self, *, name: str, slug: str) -> CreatedOrganization: ...

    async def invite_owner(self, *, external_org_id: str, email: str) -> None: ...


class ClerkAuthProvider:
    """Clerk backoffice API implementation."""

    def __init__(self, *, secret_key: str, base_url: str = "https://api.clerk.com/v1") -> None:
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._secret_key}"},
            timeout=10.0,
        )

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def create_organization(self, *, name: str, slug: str) -> CreatedOrganization:
        async with self._client() as client:
            response = await client.post("/organizations", json={"name": name, "slug": slug})
        if response.status_code >= 400:
            logger.error("clerk_create_org_failed", status=response.status_code)
            raise ProviderError(f"Clerk organization creation failed ({response.status_code}).")
        return CreatedOrganization(external_org_id=response.json()["id"])

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def invite_owner(self, *, external_org_id: str, email: str) -> None:
        async with self._client() as client:
            response = await client.post(
                f"/organizations/{external_org_id}/invitations",
                json={"email_address": email, "role": "org:admin"},
            )
        if response.status_code >= 400:
            logger.error("clerk_invite_failed", status=response.status_code)
            raise ProviderError(f"Clerk owner invitation failed ({response.status_code}).")


class NullAuthProvider:
    """No-network implementation for tests and unconfigured environments.

    Produces deterministic org IDs so the tenant can be created and the
    real organization linked later.
    """

    def __init__(self) -> None:
        self.created: list[str] = []
        self.invited: list[tuple[str, str]] = []

    async def create_organization(self, *, name: str, slug: str) -> CreatedOrganization:
        org_id = f"org_local_{slug}"
        self.created.append(org_id)
        return CreatedOrganization(external_org_id=org_id)

    async def invite_owner(self, *, external_org_id: str, email: str) -> None:
        self.invited.append((external_org_id, email))
