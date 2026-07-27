"""Admin configuration endpoints: draft → review → approve → rollback."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from ai_database.repositories import AdminContext
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import require_platform_admin
from api.db import get_session
from api.services import config_admin

router = APIRouter(prefix="/admin/tenants/{tenant_id}/configuration", tags=["admin-config"])


class ConfigVersionView(BaseModel):
    id: uuid.UUID
    version: int
    state: str
    created_by: str
    submitted_at: datetime | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_notes: str | None
    created_at: datetime


class ConfigVersionDetail(ConfigVersionView):
    payload: dict[str, Any]


class DraftRequest(BaseModel):
    payload: dict[str, Any]


class ReviewRequest(BaseModel):
    confirm: bool = False
    notes: str | None = Field(default=None, max_length=2000)


class RejectRequest(BaseModel):
    notes: str = Field(min_length=3, max_length=2000)


class RollbackRequest(BaseModel):
    confirm: bool = False
    version: int = Field(ge=1)


def _view(version: Any) -> ConfigVersionView:
    return ConfigVersionView(
        id=version.id,
        version=version.version,
        state=version.state.value,
        created_by=version.created_by,
        submitted_at=version.submitted_at,
        reviewed_by=version.reviewed_by,
        reviewed_at=version.reviewed_at,
        review_notes=version.review_notes,
        created_at=version.created_at,
    )


def _detail(version: Any) -> ConfigVersionDetail:
    return ConfigVersionDetail(**_view(version).model_dump(), payload=version.payload)


@router.get("/versions")
async def list_versions(
    tenant_id: uuid.UUID,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ConfigVersionView]:
    versions = await config_admin.list_versions(session, tenant_id=tenant_id)
    return [_view(v) for v in versions]


@router.get("/active")
async def read_active(
    tenant_id: uuid.UUID,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigVersionDetail | None:
    version = await config_admin.get_active_version(session, tenant_id)
    return _detail(version) if version else None


@router.get("/draft")
async def read_draft(
    tenant_id: uuid.UUID,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigVersionDetail | None:
    version = await config_admin.get_open_draft(session, tenant_id)
    return _detail(version) if version else None


@router.put("/draft")
async def save_draft(
    tenant_id: uuid.UUID,
    request: DraftRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigVersionView:
    version = await config_admin.save_draft(
        session, tenant_id=tenant_id, payload=request.payload, context=context
    )
    return _view(version)


@router.post("/draft/submit")
async def submit_draft(
    tenant_id: uuid.UUID,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigVersionView:
    version = await config_admin.submit_draft(session, tenant_id=tenant_id, context=context)
    return _view(version)


@router.post("/approve")
async def approve(
    tenant_id: uuid.UUID,
    request: ReviewRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigVersionView:
    from ai_shared.errors import ValidationFailedError

    if not request.confirm:
        raise ValidationFailedError("Approval requires explicit confirmation.")
    version = await config_admin.approve_version(
        session, tenant_id=tenant_id, context=context, notes=request.notes
    )
    return _view(version)


@router.post("/reject")
async def reject(
    tenant_id: uuid.UUID,
    request: RejectRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigVersionView:
    version = await config_admin.reject_version(
        session, tenant_id=tenant_id, context=context, notes=request.notes
    )
    return _view(version)


@router.post("/rollback")
async def rollback(
    tenant_id: uuid.UUID,
    request: RollbackRequest,
    context: Annotated[AdminContext, Depends(require_platform_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigVersionView:
    from ai_shared.errors import ValidationFailedError

    if not request.confirm:
        raise ValidationFailedError("Rollback requires explicit confirmation.")
    version = await config_admin.rollback_to_version(
        session, tenant_id=tenant_id, version=request.version, context=context
    )
    return _view(version)
