"""Seed a demonstration plumbing tenant.

Idempotent: keyed on the tenant slug, safe to run repeatedly. Used for
local development, staging demos, and the browser test console.

Run:  uv run python -m ai_database.seed
Env:  DATABASE_DIRECT_URL (or DATABASE_URL)
"""

import asyncio
import os
from datetime import time

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_database.engine import create_engine, create_session_factory
from ai_database.enums import MemberRole, MemberStatus, TenantStatus
from ai_database.models import (
    BusinessHours,
    PriceRule,
    Service,
    Tenant,
    TenantConfig,
    TenantMember,
)

logger = structlog.get_logger()

DEMO_SLUG = "harbor-plumbing-demo"


async def seed_demo_tenant(session: AsyncSession) -> Tenant:
    """Create (or return) the demo plumbing tenant with full configuration."""
    existing = (
        await session.execute(select(Tenant).where(Tenant.slug == DEMO_SLUG))
    ).scalar_one_or_none()
    if existing is not None:
        logger.info("seed_skipped", reason="tenant exists", slug=DEMO_SLUG)
        return existing

    tenant = Tenant(
        name="Harbor Plumbing (Demo)",
        slug=DEMO_SLUG,
        vertical="plumbing",
        timezone="America/New_York",
        status=TenantStatus.TESTING,
        plan_label="demo",
    )
    session.add(tenant)
    await session.flush()

    session.add(
        TenantConfig(
            tenant_id=tenant.id,
            greeting=(
                "Thanks for calling Harbor Plumbing! This is Alex, the "
                "automated assistant. How can I help you today?"
            ),
            persona=(
                "Friendly, efficient, plain-spoken. Short sentences. "
                "Never guesses prices or availability."
            ),
            voice_id="demo-voice-warm",
            language="en",
            recording_enabled=True,
            recording_consent_text=("This call may be recorded for quality and training purposes."),
            escalation_number="+15555550100",
            max_call_seconds=900,
            timezone="America/New_York",
            service_area={"type": "zip_list", "zips": ["02101", "02102", "02103", "02110"]},
            escalation_policy={
                "emergency_keywords": ["flood", "burst pipe", "sewage", "no water"],
                "transfer_timeout_seconds": 25,
                "after_hours_behavior": "message",
            },
        )
    )

    session.add(
        TenantMember(
            tenant_id=tenant.id,
            external_user_id="demo_owner_placeholder",
            role=MemberRole.CLIENT_OWNER,
            status=MemberStatus.INVITED,
        )
    )

    catalog: list[tuple[str, str, int, str, int | None, int | None, str]] = [
        (
            "Drain cleaning",
            "Clear a clogged sink, tub, or main drain line.",
            90,
            "drains",
            15000,
            35000,
            "range",
        ),
        (
            "Water heater repair",
            "Diagnose and repair gas or electric water heaters.",
            120,
            "water_heaters",
            22500,
            60000,
            "range",
        ),
        (
            "Leak repair",
            "Locate and fix pipe or fixture leaks.",
            90,
            "leaks",
            17500,
            45000,
            "range",
        ),
        (
            "Toilet installation",
            "Remove the old unit and install a customer-supplied toilet.",
            120,
            "fixtures",
            32500,
            32500,
            "flat",
        ),
        (
            "Emergency service call",
            "After-hours emergency dispatch fee, applied to the repair.",
            60,
            "emergency",
            15000,
            15000,
            "flat",
        ),
    ]
    for name, description, duration, category, min_cents, max_cents, unit in catalog:
        service = Service(
            tenant_id=tenant.id,
            name=name,
            name_normalized=name.strip().lower(),
            description=description,
            duration_minutes=duration,
            category=category,
        )
        session.add(service)
        await session.flush()
        session.add(
            PriceRule(
                tenant_id=tenant.id,
                service_id=service.id,
                label=f"{name} — standard",
                minimum_amount_cents=min_cents,
                maximum_amount_cents=max_cents,
                unit=unit,
                customer_visible=True,
                approved=True,
            )
        )

    # Mon–Fri 8:00–18:00, Sat 9:00–14:00, Sun closed.
    for weekday in range(7):
        if weekday <= 4:
            hours = BusinessHours(
                tenant_id=tenant.id,
                weekday=weekday,
                opens_at=time(8, 0),
                closes_at=time(18, 0),
            )
        elif weekday == 5:
            hours = BusinessHours(
                tenant_id=tenant.id,
                weekday=weekday,
                opens_at=time(9, 0),
                closes_at=time(14, 0),
            )
        else:
            hours = BusinessHours(tenant_id=tenant.id, weekday=weekday, closed=True)
        session.add(hours)

    logger.info("seed_created", slug=DEMO_SLUG, tenant_id=str(tenant.id))
    return tenant


async def main() -> None:
    url = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_DIRECT_URL (or DATABASE_URL) to seed.")
    engine = create_engine(url)
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        await seed_demo_tenant(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
