from __future__ import annotations

from fastapi import APIRouter
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
        phase=1,
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
