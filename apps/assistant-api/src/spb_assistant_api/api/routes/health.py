from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ... import __version__
from ..schemas import HealthResponse


router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="spb-assistant-api",
        version=__version__,
        phase=3,
        checks={
            "routing": "explicit",
            "query_modes": "policy,device_price",
            "memory": "disabled",
        },
    )


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
)
async def ready(request: Request) -> HealthResponse | JSONResponse:
    settings = request.app.state.settings
    checks = request.app.state.registry.readiness()
    checks.update(
        {
            "routing": "explicit",
            "memory": "disabled",
            "auth": (
                "ready"
                if not settings.auth_enabled
                or settings.parsed_api_keys()
                else "not_ready"
            ),
            "metrics": (
                "ready" if settings.metrics_enabled else "disabled"
            ),
        }
    )
    accepted = {"ready", "disabled", "explicit"}
    is_ready = all(value in accepted for value in checks.values())
    response = HealthResponse(
        status="ok" if is_ready else "not_ready",
        service="spb-assistant-api",
        version=__version__,
        phase=3,
        checks=checks,
    )
    if not is_ready:
        return JSONResponse(status_code=503, content=response.model_dump())
    return response
