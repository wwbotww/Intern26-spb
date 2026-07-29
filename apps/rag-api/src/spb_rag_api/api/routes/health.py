from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from spb_contracts import (
    COLLECTION_NAME,
    M3E_BASE_CONTRACT,
    SCHEMA_VERSION,
)

from ... import __version__
from ..schemas import HealthResponse


router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="spb-rag-api",
        version=__version__,
        phase=4,
        checks={
            "workspace": "ok",
            "collection_contract": (
                f"{COLLECTION_NAME}:v{SCHEMA_VERSION}"
            ),
            "embedding_contract": (
                f"{M3E_BASE_CONTRACT.model}:"
                f"{M3E_BASE_CONTRACT.dimension}"
            ),
        },
    )


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
)
async def ready(request: Request) -> HealthResponse | JSONResponse:
    retriever = getattr(request.app.state, "retriever", None)
    if retriever is None:
        response = HealthResponse(
            status="not_ready",
            service="spb-rag-api",
            version=__version__,
            phase=4,
            checks={
                "retriever": "not_ready",
                "reason": getattr(
                    request.app.state,
                    "initialization_error",
                    "not_initialized",
                ),
            },
        )
        return JSONResponse(
            status_code=503,
            content=response.model_dump(),
        )
    checks = retriever.readiness()
    provider = getattr(request.app.state, "chat_provider", None)
    if provider is None:
        checks["deepseek"] = "not_ready"
        checks["deepseek_reason"] = getattr(
            request.app.state,
            "chat_initialization_error",
            "not_initialized",
        )
    else:
        checks.update(provider.readiness())
    settings = request.app.state.settings
    checks["auth"] = (
        "ready"
        if not settings.auth_enabled or settings.parsed_api_keys()
        else "not_ready"
    )
    checks["metrics"] = (
        "ready" if settings.metrics_enabled else "disabled"
    )
    is_ready = all(
        value in {"ready", "disabled"}
        for value in checks.values()
    )
    response = HealthResponse(
        status="ok" if is_ready else "not_ready",
        service="spb-rag-api",
        version=__version__,
        phase=4,
        checks=checks,
    )
    if not is_ready:
        return JSONResponse(status_code=503, content=response.model_dump())
    return response
