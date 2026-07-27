# API service — build context is the repository root:
#   docker build -f services/api/Dockerfile .
# Multi-stage: uv resolves the workspace in a builder layer; the runtime
# image carries only the virtualenv and source, runs as non-root, and
# reads all secrets from the environment (never baked in).

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/api/ services/api/
RUN uv sync --frozen --no-dev --package api-service

FROM python:3.12-slim
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv .venv
COPY --from=builder --chown=app:app /app/packages packages
COPY --from=builder --chown=app:app /app/services/api services/api
COPY --chown=app:app alembic.ini alembic.ini
COPY --chown=app:app migrations/ migrations/
USER app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
EXPOSE 8080
# Exec via sh so $PORT (Cloud Run) expands; exec replaces the shell so
# SIGTERM reaches uvicorn for graceful shutdown.
CMD ["/bin/sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
