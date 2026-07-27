"""Authorization matrix tests: org→tenant mapping, membership states,
role restrictions, cross-tenant URL manipulation, suspended tenants,
admin route separation."""

from collections.abc import Callable
from typing import Any

import httpx
from tests_markers import requires_db

pytestmark = requires_db


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_unknown_org_rejected(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub="anyone", org_id="org_never_linked")
    response = await client.get("/me", headers=_auth(token))
    assert response.status_code == 403


async def test_no_org_membership_rejected(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub="anyone")  # no org, not admin
    response = await client.get("/me", headers=_auth(token))
    assert response.status_code == 403


async def test_user_not_member_of_org_rejected(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    # Valid org, but this user has no membership row.
    token = mint_token(sub="stranger", org_id=seeded_tenants["org_a"])
    response = await client.get("/me", headers=_auth(token))
    assert response.status_code == 403


async def test_inactive_membership_rejected(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub=seeded_tenants["inactive_a"], org_id=seeded_tenants["org_a"])
    response = await client.get("/me", headers=_auth(token))
    assert response.status_code == 403


async def test_suspended_tenant_blocked(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub=seeded_tenants["owner_s"], org_id=seeded_tenants["org_s"])
    response = await client.get("/me", headers=_auth(token))
    assert response.status_code == 403


async def test_owner_maps_to_own_tenant(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"])
    response = await client.get("/me", headers=_auth(token))
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "client_owner"
    assert body["tenant_id"] == str(seeded_tenants["tenant_a_id"])


async def test_cross_tenant_url_manipulation_returns_404(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    """Tenant A's owner requests tenant B's call by ID — 404, no data."""
    token = mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"])
    response = await client.get(
        f"/tenant/calls/{seeded_tenants['call_b_id']}", headers=_auth(token)
    )
    assert response.status_code == 404
    assert "error" in response.json()


async def test_owner_can_access_usage(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"])
    response = await client.get("/tenant/usage", headers=_auth(token))
    assert response.status_code == 200


async def test_staff_cannot_access_usage(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub=seeded_tenants["staff_a"], org_id=seeded_tenants["org_a"])
    response = await client.get("/tenant/usage", headers=_auth(token))
    assert response.status_code == 403


async def test_staff_can_view_tenant(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub=seeded_tenants["staff_a"], org_id=seeded_tenants["org_a"])
    response = await client.get("/tenant", headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["id"] == str(seeded_tenants["tenant_a_id"])


async def test_client_cannot_see_admin_routes(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    """Admin surface is invisible (404) to client users — not 403."""
    token = mint_token(sub=seeded_tenants["owner_a"], org_id=seeded_tenants["org_a"])
    response = await client.get("/admin/tenants", headers=_auth(token))
    assert response.status_code == 404


async def test_admin_can_list_tenants(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub="admin_user", platform_role="platform_admin")
    response = await client.get("/admin/tenants", headers=_auth(token))
    assert response.status_code == 200
    slugs = {t["slug"] for t in response.json()}
    assert f"auth-a-{seeded_tenants['suffix']}" in slugs
    assert f"auth-b-{seeded_tenants['suffix']}" in slugs


async def test_admin_reads_specific_tenant(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    token = mint_token(sub="admin_user", platform_role="platform_admin")
    response = await client.get(
        f"/admin/tenants/{seeded_tenants['tenant_b_id']}", headers=_auth(token)
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(seeded_tenants["tenant_b_id"])


async def test_admin_has_no_client_tenant_view(
    client: httpx.AsyncClient, mint_token: Callable[..., str], seeded_tenants: dict[str, Any]
) -> None:
    """Admin tokens carry no tenant scope; client routes refuse them."""
    token = mint_token(sub="admin_user", platform_role="platform_admin")
    response = await client.get("/tenant", headers=_auth(token))
    assert response.status_code == 403
