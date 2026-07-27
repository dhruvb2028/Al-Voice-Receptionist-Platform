"""Row-level security backstop tests.

Verified under a dedicated non-owner role (RLS does not bind to the
table owner), exactly how the application role will connect in
production: without ``app.tenant_id`` set the role sees zero rows; with
it set it sees exactly one tenant's rows.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def rls_role_url(admin_url: str) -> str:
    """Same server/database, connecting as the RLS-enforced test role."""
    from sqlalchemy.engine import make_url

    url = make_url(admin_url).set(username="app_rls_test", password="rlstest")
    # str(URL) masks the password as '***'; render it usable.
    return url.render_as_string(hide_password=False)


@pytest.fixture
async def rls_setup(migrated_database: str) -> AsyncIterator[tuple[str, uuid.UUID, uuid.UUID]]:
    """Create an RLS-bound role and two tenants with one call each.

    Committed data (RLS is evaluated per role/connection), cleaned up at
    teardown.
    """
    admin_engine = create_async_engine(migrated_database)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "DO $$ BEGIN CREATE ROLE app_rls_test; "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
            )
        )
        # Always (re)set login + password: the role may exist from a prior
        # run with different credentials.
        await conn.execute(text("ALTER ROLE app_rls_test WITH LOGIN PASSWORD 'rlstest'"))
        # Schema USAGE must be granted explicitly: a recreated public
        # schema is owner-only by default.
        await conn.execute(text("GRANT USAGE ON SCHEMA public TO app_rls_test"))
        await conn.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE "
                "ON ALL TABLES IN SCHEMA public TO app_rls_test"
            )
        )
        for tid, slug in ((tenant_a, f"rls-a-{suffix}"), (tenant_b, f"rls-b-{suffix}")):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, vertical, timezone, status) "
                    "VALUES (:id, :name, :slug, 'plumbing', 'UTC', 'testing')"
                ),
                {"id": tid, "name": f"RLS {slug}", "slug": slug},
            )
            await conn.execute(
                text(
                    "INSERT INTO calls (id, tenant_id, provider_call_sid, to_number, "
                    "started_at, direction, recording_status, transcript_status, "
                    "post_processing_status) VALUES (:id, :tid, :sid, '+15555550100', "
                    ":now, 'inbound', 'disabled', 'pending', 'pending')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tid": tid,
                    "sid": f"CA_rls_{tid.hex[:10]}",
                    "now": datetime.now(UTC),
                },
            )

    try:
        yield rls_role_url(migrated_database), tenant_a, tenant_b
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text("DELETE FROM calls WHERE provider_call_sid LIKE 'CA_rls_%'"))
            await conn.execute(
                text("DELETE FROM tenants WHERE slug LIKE :pat"), {"pat": f"rls-%-{suffix}"}
            )
        await admin_engine.dispose()


async def test_rls_blocks_unscoped_queries(
    rls_setup: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    url, _, _ = rls_setup
    engine = create_async_engine(url)
    async with AsyncSession(engine) as session:
        count = (await session.execute(text("SELECT count(*) FROM calls"))).scalar_one()
    await engine.dispose()
    # No app.tenant_id set -> the policy matches nothing.
    assert count == 0


async def test_rls_scopes_to_bound_tenant(
    rls_setup: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    url, tenant_a, tenant_b = rls_setup
    engine = create_async_engine(url)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_a)}
        )
        rows = (await session.execute(text("SELECT tenant_id FROM calls"))).scalars().all()
    await engine.dispose()
    assert rows, "bound tenant should see its own rows"
    assert all(row == tenant_a for row in rows)
    assert tenant_b not in rows


async def test_rls_blocks_cross_tenant_insert(
    rls_setup: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    url, tenant_a, tenant_b = rls_setup
    engine = create_async_engine(url)
    with pytest.raises(Exception, match="row-level security|RowSecurityError"):
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_a)}
            )
            # Bound to A, attempting to write a row for B -> WITH CHECK fails.
            await session.execute(
                text(
                    "INSERT INTO calls (id, tenant_id, provider_call_sid, to_number, "
                    "started_at, direction, recording_status, transcript_status, "
                    "post_processing_status) VALUES (:id, :tid, :sid, '+15555550101', "
                    "now(), 'inbound', 'disabled', 'pending', 'pending')"
                ),
                {"id": uuid.uuid4(), "tid": tenant_b, "sid": f"CA_rls_x_{uuid.uuid4().hex[:8]}"},
            )
    await engine.dispose()
