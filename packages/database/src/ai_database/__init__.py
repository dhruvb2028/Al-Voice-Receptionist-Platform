"""Database access layer: engine, models, tenancy, repositories."""

from ai_database.engine import create_engine, create_session_factory
from ai_database.metadata import Base
from ai_database.repositories import (
    AdminContext,
    AdminRepository,
    TenantScopedRepository,
)
from ai_database.tenancy import set_tenant_context

__all__ = [
    "AdminContext",
    "AdminRepository",
    "Base",
    "TenantScopedRepository",
    "create_engine",
    "create_session_factory",
    "set_tenant_context",
]
