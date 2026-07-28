from __future__ import annotations

from fastapi import FastAPI

from .. import __version__
from .routes.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="SPB Policy RAG API",
        version=__version__,
        description="国家邮政局政策知识库在线检索与问答服务",
    )
    app.include_router(health_router)
    return app
