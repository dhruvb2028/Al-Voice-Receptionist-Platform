"""Link a Clerk organisation and user to the demo tenant.

Signing in with a fresh Clerk account gets a 403 until the database
knows which tenant that organisation maps to. Authorization is derived
entirely from persisted rows — a valid Clerk token proves who you are,
never what you may see — so the mapping is a deliberate step rather than
something a first sign-in creates for itself.

Run:
    uv run python -m ai_database.link_clerk --org-id org_xxx --user-id user_xxx

Both ids come from the Clerk dashboard, or from the JWT of a signed-in
session. Idempotent: re-running updates the same rows.

To grant platform-admin instead, set PLATFORM_ADMIN_USER_IDS on the API
rather than using this script — admin is not a tenant membership.
"""

import argparse
import asyncio
import os

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_database.engine import create_engine, create_session_factory
from ai_database.enums import MemberRole, MemberStatus
from ai_database.models import Tenant, TenantMember
from ai_database.seed import DEMO_SLUG

logger = structlog.get_logger()


async def link_clerk_identity(
    session: AsyncSession,
    *,
    org_id: str,
    user_id: str,
    slug: str = DEMO_SLUG,
    role: MemberRole = MemberRole.CLIENT_OWNER,
) -> Tenant:
    """Point a tenant at a Clerk org and activate one member on it."""
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        raise SystemExit(f"No tenant with slug '{slug}'. Run `python -m ai_database.seed` first.")

    clash = (
        await session.execute(
            select(Tenant).where(Tenant.external_auth_org_id == org_id, Tenant.id != tenant.id)
        )
    ).scalar_one_or_none()
    if clash is not None:
        # external_auth_org_id is unique, and one org mapping to two
        # tenants would be an isolation hole, not a convenience.
        raise SystemExit(f"Organisation {org_id} is already linked to tenant '{clash.slug}'.")

    tenant.external_auth_org_id = org_id

    member = (
        await session.execute(
            select(TenantMember).where(
                TenantMember.tenant_id == tenant.id,
                TenantMember.external_user_id == user_id,
            )
        )
    ).scalar_one_or_none()

    if member is None:
        # The seed leaves a placeholder INVITED member; reuse it so the
        # tenant does not accumulate a dead row.
        placeholder = (
            await session.execute(
                select(TenantMember).where(
                    TenantMember.tenant_id == tenant.id,
                    TenantMember.external_user_id == "demo_owner_placeholder",
                )
            )
        ).scalar_one_or_none()
        if placeholder is not None:
            placeholder.external_user_id = user_id
            placeholder.role = role
            placeholder.status = MemberStatus.ACTIVE
            member = placeholder
        else:
            member = TenantMember(
                tenant_id=tenant.id,
                external_user_id=user_id,
                role=role,
                status=MemberStatus.ACTIVE,
            )
            session.add(member)
    else:
        member.role = role
        member.status = MemberStatus.ACTIVE

    await session.flush()
    logger.info("clerk_identity_linked", slug=slug, role=role.value)
    return tenant


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True, help="Clerk organisation id (org_...)")
    parser.add_argument("--user-id", required=True, help="Clerk user id (user_...)")
    parser.add_argument("--slug", default=DEMO_SLUG)
    parser.add_argument(
        "--role",
        default="client_owner",
        choices=[r.value for r in MemberRole],
    )
    args = parser.parse_args()

    url = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_DIRECT_URL (or DATABASE_URL).")

    engine = create_engine(url)
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        tenant = await link_clerk_identity(
            session,
            org_id=args.org_id,
            user_id=args.user_id,
            slug=args.slug,
            role=MemberRole(args.role),
        )
        name = tenant.name
    await engine.dispose()
    print(f"Linked Clerk org {args.org_id} to '{name}' as {args.role}.")


if __name__ == "__main__":
    asyncio.run(main())
