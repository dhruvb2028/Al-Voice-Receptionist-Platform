"""Identity endpoint: who am I, in which role, for which tenant."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth.dependencies import get_principal
from api.auth.models import Principal

router = APIRouter(tags=["identity"])


class MeResponse(BaseModel):
    external_user_id: str
    role: str
    tenant_id: uuid.UUID | None


@router.get("/me")
async def read_me(principal: Annotated[Principal, Depends(get_principal)]) -> MeResponse:
    return MeResponse(
        external_user_id=principal.external_user_id,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
    )
