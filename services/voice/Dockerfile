# Voice service — build context is the repository root:
#   docker build -f services/voice/Dockerfile .
# Holds long-lived WebSockets; Cloud Run must run it with CPU always
# allocated and a long request timeout. Graceful shutdown lets in-flight
# calls finish (uvicorn drains on SIGTERM).

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/voice/ services/voice/
RUN uv sync --frozen --no-dev --package voice-service

FROM python:3.12-slim
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv .venv
COPY --from=builder --chown=app:app /app/packages packages
COPY --from=builder --chown=app:app /app/services/voice services/voice
USER app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["/bin/sh", "-c", "exec uvicorn voice.main:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-graceful-shutdown 3600"]
