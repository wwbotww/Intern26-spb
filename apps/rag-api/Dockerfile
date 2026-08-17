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
    --package spb-rag-api \
    --no-install-workspace

ARG EMBEDDING_MODEL=moka-ai/m3e-base
ARG RERANK_MODEL=BAAI/bge-reranker-base
ENV EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    RERANK_MODEL=${RERANK_MODEL} \
    HF_HOME=/opt/huggingface
RUN /app/.venv/bin/python -c \
    'import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ["EMBEDDING_MODEL"], device="cpu")'
RUN /app/.venv/bin/python -c \
    'import os; from transformers import AutoModelForSequenceClassification, AutoTokenizer; name=os.environ["RERANK_MODEL"]; AutoTokenizer.from_pretrained(name); AutoModelForSequenceClassification.from_pretrained(name)'

COPY apps/rag-api/src apps/rag-api/src
COPY packages/contracts/src packages/contracts/src

RUN uv sync \
    --frozen \
    --no-dev \
    --package spb-rag-api \
    --no-editable


FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    RAG_HOST=0.0.0.0 \
    RAG_PORT=8080

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=builder --chown=10001:10001 /opt/huggingface /opt/huggingface

USER 10001:10001
EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=3)"]

CMD ["/app/.venv/bin/spb-rag-api"]
