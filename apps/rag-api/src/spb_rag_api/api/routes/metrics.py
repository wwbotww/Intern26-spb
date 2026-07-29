from __future__ import annotations

from fastapi import APIRouter, Request, Response


router = APIRouter(tags=["operations"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    service_metrics = request.app.state.metrics
    return Response(
        content=service_metrics.render(),
        headers={"Content-Type": service_metrics.content_type},
    )
