"""Client onboarding workflow.

Onboarding is a checklist an administrator works through, and its state
is **derived** rather than stored wherever possible: a step is complete
because the underlying rows exist, not because someone ticked a box. That
matters because a checklist that can disagree with reality is worse than
no checklist — it produces confident activation of a broken tenant.

The exceptions are the judgement steps (a greeting approved, a phone test
heard by a human) that nothing in the database can prove. Those are
recorded explicitly, with an audit entry naming who attested to them.

Everything here is data-driven, so onboarding a second tenant is
configuration, never a code change.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ai_database.audit import write_audit
from ai_database.enums import CalendarConnectionStatus, MemberStatus, TenantStatus
from ai_database.models import (
    BusinessHours,
    CalendarConnection,
    PhoneNumber,
    PriceRule,
    Service,
    Tenant,
    TenantConfig,
    TenantMember,
)
from ai_shared.errors import NotFoundError, ValidationFailedError
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.admin_tenants import ActivationReadiness
from api.services.tenant_admin import activation_readiness


class StepStatus(StrEnum):
    COMPLETE = "complete"
    BLOCKED = "blocked"
    PENDING = "pending"


@dataclass(frozen=True)
class StepSpec:
    key: str
    title: str
    description: str
    #: an attested step is recorded by a human; the rest are derived
    attested: bool = False
    #: activation may proceed without it, with a recorded justification
    waivable: bool = False


STEPS: tuple[StepSpec, ...] = (
    StepSpec("business_information", "Business information", "Name, timezone, and vertical."),
    StepSpec("owner_invitation", "Owner invitation", "The owner has an active dashboard login."),
    StepSpec("phone_number", "Phone number", "A number is assigned and routed to the platform."),
    StepSpec(
        "greeting",
        "Greeting",
        "The greeting is written and the client has approved the wording.",
        attested=True,
    ),
    StepSpec(
        "recording_notice",
        "Recording notice",
        "If recording is on, the consent announcement is configured.",
    ),
    StepSpec("services", "Services", "At least one active service the business offers."),
    StepSpec(
        "prices",
        "Prices",
        "Customer-visible prices are approved. The receptionist can quote nothing else.",
    ),
    StepSpec("business_hours", "Business hours", "Opening hours for every day of the week."),
    StepSpec("service_area", "Service area", "Where the business will travel."),
    StepSpec(
        "escalation",
        "Escalation",
        "An escalation number is set and a test dial reached a human.",
        attested=True,
    ),
    StepSpec("calendar", "Calendar", "Google Calendar is connected and healthy."),
    StepSpec("voice", "Voice", "A voice is selected for the receptionist."),
    StepSpec(
        "browser_text_test",
        "Browser text test",
        "A scripted conversation passed in the text simulator.",
        attested=True,
    ),
    StepSpec(
        "browser_voice_test",
        "Browser voice test",
        "Audio confirmed in the browser before dialling a real number.",
        attested=True,
        waivable=True,
    ),
    StepSpec(
        "real_phone_test",
        "Real phone test",
        "A real call to the assigned number, heard end to end by a person.",
        attested=True,
        waivable=True,
    ),
    StepSpec(
        "safety_review",
        "Safety review",
        "Configuration approved: prices, escalation, and guardrail behaviour.",
        attested=True,
    ),
    StepSpec(
        "activation",
        "Activation",
        "Tenant set live. Requires every blocker cleared.",
        attested=True,
    ),
)

STEPS_BY_KEY = {step.key: step for step in STEPS}

#: activation_state keys recording a human attestation per step
_ATTESTATION_KEY = {
    "greeting": "greeting_approved",
    "escalation": "escalation_verified",
    "browser_text_test": "browser_test_passed",
    "browser_voice_test": "browser_voice_test_passed",
    "real_phone_test": "phone_test_passed",
    "safety_review": "safety_reviewed",
}
_WAIVER_KEY = {
    "browser_voice_test": "browser_voice_test_waived",
    "real_phone_test": "phone_test_waived",
}


class StepState(BaseModel):
    key: str
    title: str
    description: str
    status: StepStatus
    attested: bool
    waivable: bool
    detail: str = ""
    attested_by: str | None = None
    attested_at: datetime | None = None
    waived: bool = False
    waiver_reason: str | None = None


class OnboardingState(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_status: str
    steps: list[StepState]
    readiness: ActivationReadiness
    completed_steps: int
    total_steps: int

    @property
    def ready_to_activate(self) -> bool:
        return self.readiness.ready


async def _facts(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Everything the derived steps are computed from, in one pass."""
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Tenant not found.")
    config = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    ).scalar_one_or_none()

    async def count(model: Any, *where: Any) -> int:
        return int(
            (
                await session.execute(select(func.count()).select_from(model).where(*where))
            ).scalar_one()
        )

    calendar = (
        await session.execute(
            select(CalendarConnection).where(CalendarConnection.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()

    return {
        "tenant": tenant,
        "config": config,
        "state": (config.activation_state or {}) if config else {},
        "active_members": await count(
            TenantMember,
            TenantMember.tenant_id == tenant_id,
            TenantMember.status == MemberStatus.ACTIVE,
        ),
        "numbers": await count(
            PhoneNumber, PhoneNumber.tenant_id == tenant_id, PhoneNumber.active.is_(True)
        ),
        "services": await count(Service, Service.tenant_id == tenant_id, Service.active.is_(True)),
        "approved_prices": await count(
            PriceRule, PriceRule.tenant_id == tenant_id, PriceRule.approved.is_(True)
        ),
        "hours": await count(BusinessHours, BusinessHours.tenant_id == tenant_id),
        "calendar": calendar,
    }


def _derived_status(step: StepSpec, facts: dict[str, Any]) -> tuple[StepStatus, str]:
    """Status of a step that the database can answer on its own."""
    config: TenantConfig | None = facts["config"]
    tenant: Tenant = facts["tenant"]

    if step.key == "business_information":
        ok = bool(tenant.name and tenant.timezone and tenant.vertical)
        return (StepStatus.COMPLETE if ok else StepStatus.PENDING, "")
    if step.key == "owner_invitation":
        count = facts["active_members"]
        return (
            (StepStatus.COMPLETE, f"{count} active member(s)")
            if count
            else (StepStatus.PENDING, "No active member has signed in yet")
        )
    if step.key == "phone_number":
        count = facts["numbers"]
        return (
            (StepStatus.COMPLETE, f"{count} number(s) assigned")
            if count
            else (StepStatus.BLOCKED, "No active number assigned")
        )
    if step.key == "recording_notice":
        if config is None or not config.recording_enabled:
            return (StepStatus.COMPLETE, "Recording is off for this tenant")
        return (
            (StepStatus.COMPLETE, "")
            if (config.recording_consent_text or "").strip()
            else (StepStatus.BLOCKED, "Recording is on but no consent notice is set")
        )
    if step.key == "services":
        count = facts["services"]
        return (
            (StepStatus.COMPLETE, f"{count} active service(s)")
            if count
            else (StepStatus.BLOCKED, "No active services")
        )
    if step.key == "prices":
        count = facts["approved_prices"]
        # Not a blocker: a business may legitimately quote nothing by
        # phone. The receptionist simply refuses to give a price.
        return (
            (StepStatus.COMPLETE, f"{count} approved price(s)")
            if count
            else (StepStatus.PENDING, "No approved prices — the receptionist will quote none")
        )
    if step.key == "business_hours":
        count = facts["hours"]
        return (
            (StepStatus.COMPLETE, f"{count} day(s) configured")
            if count
            else (StepStatus.BLOCKED, "Business hours are not configured")
        )
    if step.key == "service_area":
        area = (config.service_area if config else None) or {}
        return (
            (StepStatus.COMPLETE, "")
            if area
            else (StepStatus.PENDING, "No service area — the receptionist cannot decline by area")
        )
    if step.key == "calendar":
        calendar: CalendarConnection | None = facts["calendar"]
        if calendar is None:
            return (StepStatus.BLOCKED, "Calendar is not connected")
        if calendar.status is not CalendarConnectionStatus.CONNECTED:
            return (StepStatus.BLOCKED, f"Calendar is {calendar.status.value}")
        return (StepStatus.COMPLETE, "")
    if step.key == "voice":
        return (
            (StepStatus.COMPLETE, "")
            if config and config.voice_id
            else (StepStatus.PENDING, "No voice selected")
        )
    if step.key == "activation":
        return (
            (StepStatus.COMPLETE, "Live")
            if tenant.status is TenantStatus.ACTIVE
            else (StepStatus.PENDING, f"Tenant is {tenant.status.value}")
        )
    return (StepStatus.PENDING, "")


def _attested_status(step: StepSpec, facts: dict[str, Any]) -> tuple[StepStatus, str]:
    state: dict[str, Any] = facts["state"]
    config: TenantConfig | None = facts["config"]

    if step.key == "greeting" and (config is None or not (config.greeting or "").strip()):
        return (StepStatus.BLOCKED, "No greeting has been written")
    if step.key == "escalation" and (config is None or not config.escalation_number):
        return (StepStatus.BLOCKED, "No escalation number is set")
    if step.key == "safety_review" and config is not None and config.approved_at is not None:
        return (StepStatus.COMPLETE, "Configuration approved")

    if state.get(_ATTESTATION_KEY.get(step.key, "")):
        return (StepStatus.COMPLETE, "")
    if step.waivable and state.get(_WAIVER_KEY.get(step.key, "")):
        return (StepStatus.COMPLETE, "Waived")
    return (StepStatus.PENDING, "Awaiting sign-off")


async def onboarding_state(session: AsyncSession, tenant_id: uuid.UUID) -> OnboardingState:
    facts = await _facts(session, tenant_id)
    state: dict[str, Any] = facts["state"]
    steps: list[StepState] = []

    for spec in STEPS:
        if spec.key == "activation" or not spec.attested:
            status, detail = _derived_status(spec, facts)
        else:
            status, detail = _attested_status(spec, facts)
        if spec.key == "activation":
            status, detail = _derived_status(spec, facts)

        record = state.get(f"{spec.key}_record") or {}
        waiver = state.get(f"{spec.key}_waiver") or {}
        steps.append(
            StepState(
                key=spec.key,
                title=spec.title,
                description=spec.description,
                status=status,
                attested=spec.attested,
                waivable=spec.waivable,
                detail=detail,
                attested_by=record.get("by"),
                attested_at=record.get("at"),
                waived=bool(waiver),
                waiver_reason=waiver.get("reason"),
            )
        )

    readiness = await activation_readiness(session, tenant_id=tenant_id)
    return OnboardingState(
        tenant_id=tenant_id,
        tenant_name=facts["tenant"].name,
        tenant_status=facts["tenant"].status.value,
        steps=steps,
        readiness=readiness,
        completed_steps=sum(1 for s in steps if s.status is StepStatus.COMPLETE),
        total_steps=len(steps),
    )


async def record_step(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    step_key: str,
    passed: bool,
    actor_external_user_id: str | None,
    actor_role: str | None,
    note: str | None = None,
) -> OnboardingState:
    """Attest that a human-judgement step passed (or un-attest it)."""
    spec = STEPS_BY_KEY.get(step_key)
    if spec is None:
        raise ValidationFailedError(f"Unknown onboarding step '{step_key}'.")
    if not spec.attested:
        raise ValidationFailedError(
            f"Step '{step_key}' is derived from configuration and cannot be ticked by hand."
        )
    if step_key == "activation":
        raise ValidationFailedError("Activation happens through the lifecycle endpoint.")

    config = await _require_config(session, tenant_id)
    state = dict(config.activation_state or {})
    flag = _ATTESTATION_KEY[step_key]
    now = datetime.now(UTC)

    if passed:
        state[flag] = True
        state[f"{step_key}_record"] = {
            "by": actor_external_user_id,
            "at": now.isoformat(),
            "note": note,
        }
    else:
        state.pop(flag, None)
        state.pop(f"{step_key}_record", None)

    config.activation_state = state
    await write_audit(
        session,
        action="onboarding.step_recorded",
        actor_external_user_id=actor_external_user_id,
        actor_role=actor_role,
        tenant_id=tenant_id,
        resource_type="onboarding_step",
        resource_id=step_key,
        after={"passed": passed, "note": note},
    )
    await session.flush()
    return await onboarding_state(session, tenant_id)


async def waive_step(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    step_key: str,
    reason: str,
    actor_external_user_id: str | None,
    actor_role: str | None,
) -> OnboardingState:
    """Skip a waivable step, recording who decided and why.

    A waiver without a reason is just a skipped check, so the reason is
    required rather than optional.
    """
    spec = STEPS_BY_KEY.get(step_key)
    if spec is None:
        raise ValidationFailedError(f"Unknown onboarding step '{step_key}'.")
    if not spec.waivable:
        raise ValidationFailedError(f"Step '{step_key}' cannot be waived.")
    if not reason.strip():
        raise ValidationFailedError("A waiver requires a justification.")

    config = await _require_config(session, tenant_id)
    state = dict(config.activation_state or {})
    state[_WAIVER_KEY[step_key]] = True
    state[f"{step_key}_waiver"] = {
        "by": actor_external_user_id,
        "at": datetime.now(UTC).isoformat(),
        "reason": reason.strip(),
    }
    config.activation_state = state

    await write_audit(
        session,
        action="onboarding.step_waived",
        actor_external_user_id=actor_external_user_id,
        actor_role=actor_role,
        tenant_id=tenant_id,
        resource_type="onboarding_step",
        resource_id=step_key,
        after={"reason": reason.strip()},
    )
    await session.flush()
    return await onboarding_state(session, tenant_id)


async def _require_config(session: AsyncSession, tenant_id: uuid.UUID) -> TenantConfig:
    config = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if config is None:
        raise ValidationFailedError(
            "Tenant configuration must exist before onboarding steps are recorded."
        )
    return config


# --- reports -----------------------------------------------------------------


class OnboardingReport(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    generated_at: datetime
    title: str
    sections: list[dict[str, Any]]


async def handover_checklist(session: AsyncSession, tenant_id: uuid.UUID) -> OnboardingReport:
    """What the client needs to know before their number goes live."""
    facts = await _facts(session, tenant_id)
    config: TenantConfig | None = facts["config"]
    services = (
        (
            await session.execute(
                select(Service)
                .where(Service.tenant_id == tenant_id, Service.active.is_(True))
                .order_by(Service.name)
            )
        )
        .scalars()
        .all()
    )
    return OnboardingReport(
        tenant_id=tenant_id,
        tenant_name=facts["tenant"].name,
        generated_at=datetime.now(UTC),
        title="Client handover checklist",
        sections=[
            {
                "heading": "What your receptionist will do",
                "items": [
                    f"Answer as: {(config.greeting or '').strip()[:160]}" if config else "",
                    f"Book these services: {', '.join(s.name for s in services) or 'none'}",
                    f"Transfer emergencies to: ···{(config.escalation_number or '')[-4:]}"
                    if config and config.escalation_number
                    else "No escalation number set",
                ],
            },
            {
                "heading": "What it will not do",
                "items": [
                    "Quote any price you have not approved.",
                    "Promise a time your calendar does not have free.",
                    "Take work outside your service area.",
                    "Read internal notes aloud to a caller.",
                ],
            },
            {
                "heading": "Your responsibilities",
                "items": [
                    "Answer transfers — an emergency reaches you, not voicemail.",
                    "Keep your calendar current; it is the source of truth for availability.",
                    "Tell us when prices or services change, so the receptionist stays accurate.",
                    "Review messages in the dashboard daily.",
                ],
            },
            {
                "heading": "Recording and privacy",
                "items": [
                    f"Recording is {'ON' if config and config.recording_enabled else 'OFF'}.",
                    f"Recordings kept for {config.recording_retention_days or 30} days."
                    if config and config.recording_enabled
                    else "No recordings are stored.",
                    "Add this platform to your own privacy notice.",
                ],
            },
        ],
    )


async def test_call_report(session: AsyncSession, tenant_id: uuid.UUID) -> OnboardingReport:
    """What was tested, by whom, and what was waived."""
    facts = await _facts(session, tenant_id)
    state: dict[str, Any] = facts["state"]

    tests = []
    for key in ("browser_text_test", "browser_voice_test", "real_phone_test", "escalation"):
        spec = STEPS_BY_KEY[key]
        record = state.get(f"{key}_record") or {}
        waiver = state.get(f"{key}_waiver") or {}
        passed = bool(state.get(_ATTESTATION_KEY[key]))
        tests.append(
            {
                "test": spec.title,
                "result": "passed" if passed else ("waived" if waiver else "not run"),
                "by": record.get("by") or waiver.get("by"),
                "at": record.get("at") or waiver.get("at"),
                "note": record.get("note") or waiver.get("reason"),
            }
        )

    return OnboardingReport(
        tenant_id=tenant_id,
        tenant_name=facts["tenant"].name,
        generated_at=datetime.now(UTC),
        title="Test call report",
        sections=[{"heading": "Tests", "items": tests}],
    )


async def activation_report(session: AsyncSession, tenant_id: uuid.UUID) -> OnboardingReport:
    """The record of why this tenant was considered safe to go live."""
    state_obj = await onboarding_state(session, tenant_id)
    facts = await _facts(session, tenant_id)
    config: TenantConfig | None = facts["config"]

    return OnboardingReport(
        tenant_id=tenant_id,
        tenant_name=state_obj.tenant_name,
        generated_at=datetime.now(UTC),
        title="Activation report",
        sections=[
            {
                "heading": "Summary",
                "items": [
                    f"Status: {state_obj.tenant_status}",
                    f"Steps complete: {state_obj.completed_steps}/{state_obj.total_steps}",
                    f"Ready to activate: {'yes' if state_obj.readiness.ready else 'no'}",
                    f"Configuration approved at: {config.approved_at.isoformat()}"
                    if config and config.approved_at
                    else "Configuration not approved",
                ],
            },
            {
                "heading": "Outstanding blockers",
                "items": [
                    {"code": b.code, "message": b.message, "waivable": b.waivable}
                    for b in state_obj.readiness.blockers
                ]
                or ["None"],
            },
            {
                "heading": "Steps",
                "items": [
                    {
                        "step": s.title,
                        "status": s.status.value,
                        "detail": s.detail,
                        "by": s.attested_by,
                        "waived": s.waived,
                        "waiver_reason": s.waiver_reason,
                    }
                    for s in state_obj.steps
                ],
            },
        ],
    )


__all__ = [
    "STEPS",
    "STEPS_BY_KEY",
    "OnboardingReport",
    "OnboardingState",
    "StepSpec",
    "StepState",
    "StepStatus",
    "activation_report",
    "handover_checklist",
    "onboarding_state",
    "record_step",
    "test_call_report",
    "waive_step",
]
