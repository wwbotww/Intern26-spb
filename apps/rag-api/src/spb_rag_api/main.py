from __future__ import annotations

import uvicorn

from .api.app import create_app
from .observability.logging import configure_logging
from .settings import ApiSettings


app = create_app()


def run() -> None:
    settings = ApiSettings()
    configure_logging(
        level=settings.log_level,
        json_output=settings.log_json,
    )
    uvicorn.run(
        "spb_rag_api.main:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=settings.log_level,
        log_config=None,
        access_log=False,
    )
