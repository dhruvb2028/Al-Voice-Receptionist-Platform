"""Worker service entrypoint.

Receives signed QStash deliveries at the post-call milestone; the
bootstrap version provides health and readiness only.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from ai_shared.fastapi_setup import configure_service_app
from ai_telemetry import configure_logging
from fastapi import FastAPI

from worker.settings import get_settings

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(
        service_name=settings.service_name,
        log_level=settings.log_level,
        json_output=settings.environment.value != "local",
    )
    logger.info("service_starting", environment=settings.environment.value)
    yield
    logger.info("service_stopping")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Receptionist Worker",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    return configure_service_app(app, service_name="worker")


app = create_app()
