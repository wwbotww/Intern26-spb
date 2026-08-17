from __future__ import annotations

import uvicorn

from .api.app import create_app
from .observability.logging import configure_logging
from .settings import AssistantSettings


app = create_app()


def run() -> None:
    settings = AssistantSettings()
    configure_logging(
        level=settings.log_level,
        json_output=settings.log_json,
    )
    uvicorn.run(
        "spb_assistant_api.main:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=settings.log_level,
        log_config=None,
        access_log=False,
    )
