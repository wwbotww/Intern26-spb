from __future__ import annotations

import uvicorn

from .api.app import create_app
from .settings import ApiSettings


app = create_app()


def run() -> None:
    settings = ApiSettings()
    uvicorn.run(
        "spb_rag_api.main:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=settings.log_level,
    )
