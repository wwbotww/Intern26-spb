FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_HTTP_TIMEOUT=120 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY apps/offline-pipeline/pyproject.toml apps/offline-pipeline/pyproject.toml
COPY apps/rag-api/pyproject.toml apps/rag-api/pyproject.toml
COPY apps/assistant-api/pyproject.toml apps/assistant-api/pyproject.toml
COPY eval/pyproject.toml eval/pyproject.toml
COPY packages/contracts/pyproject.toml packages/contracts/pyproject.toml

RUN uv sync \
    --frozen \
    --no-dev \
    --package spb-assistant-api \
    --no-install-workspace

COPY apps/assistant-api/src apps/assistant-api/src

RUN uv sync \
    --frozen \
    --no-dev \
    --package spb-assistant-api \
    --no-editable


FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ASSISTANT_HOST=0.0.0.0 \
    ASSISTANT_PORT=8081

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

USER 10001:10001
EXPOSE 8081
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/health/live', timeout=3)"]

CMD ["/app/.venv/bin/spb-assistant-api"]
