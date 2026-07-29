from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .. import __version__
from ..adapters.deepseek import DeepSeekChatProvider, DeepSeekConfig
from ..adapters.embedding import SentenceTransformerQueryEmbedder
from ..adapters.milvus import MilvusHybridSearchStore, MilvusReadConfig
from ..domain.ports import ChatProvider, Retriever
from ..services.search import HybridSearchService
from ..settings import ApiSettings
from .routes.chat import router as chat_router
from .routes.health import router as health_router
from .routes.search import router as search_router


logger = logging.getLogger(__name__)


def _build_retriever(settings: ApiSettings) -> HybridSearchService:
    embedder = SentenceTransformerQueryEmbedder(
        model_name=settings.embedding_model,
        device=settings.embedding_device,
        max_concurrency=settings.max_concurrency,
    )
    store = MilvusHybridSearchStore(
        MilvusReadConfig(
            uri=settings.milvus_uri,
            token=settings.milvus_token.get_secret_value(),
            database=settings.milvus_database,
            collection=settings.milvus_collection,
            timeout_seconds=settings.milvus_timeout_seconds,
            consistency_level=settings.milvus_consistency_level,
            rrf_k=settings.search_rrf_k,
            dense_ef=settings.search_dense_ef,
        )
    )
    return HybridSearchService(embedder=embedder, store=store)


def _build_chat_provider(settings: ApiSettings) -> DeepSeekChatProvider:
    return DeepSeekChatProvider(
        DeepSeekConfig(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key.get_secret_value(),
            model=settings.deepseek_model,
            thinking=settings.deepseek_thinking,
            timeout_seconds=settings.deepseek_timeout_seconds,
            max_tokens=settings.deepseek_max_tokens,
            temperature=settings.deepseek_temperature,
        )
    )


def create_app(
    *,
    settings: ApiSettings | None = None,
    retriever: Retriever | None = None,
    chat_provider: ChatProvider | None = None,
) -> FastAPI:
    resolved_settings = settings or ApiSettings()
    managed_retriever: Retriever | None = None
    managed_chat_provider: ChatProvider | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal managed_chat_provider, managed_retriever
        app.state.settings = resolved_settings
        app.state.capacity = asyncio.Semaphore(
            resolved_settings.max_concurrency
        )
        app.state.initialization_error = ""
        if retriever is not None:
            app.state.retriever = retriever
        elif not resolved_settings.milvus_uri:
            app.state.retriever = None
            app.state.initialization_error = "milvus_not_configured"
        elif not resolved_settings.initialize_on_startup:
            app.state.retriever = None
            app.state.initialization_error = "startup_initialization_disabled"
        else:
            managed_retriever = _build_retriever(resolved_settings)
            try:
                await managed_retriever.initialize()
            except Exception as exc:
                logger.exception("retrieval initialization failed")
                app.state.retriever = None
                app.state.initialization_error = type(exc).__name__
                await managed_retriever.close()
                managed_retriever = None
            else:
                app.state.retriever = managed_retriever
        app.state.chat_initialization_error = ""
        if chat_provider is not None:
            app.state.chat_provider = chat_provider
        elif not resolved_settings.deepseek_api_key.get_secret_value():
            app.state.chat_provider = None
            app.state.chat_initialization_error = "deepseek_not_configured"
        else:
            managed_chat_provider = _build_chat_provider(
                resolved_settings
            )
            app.state.chat_provider = managed_chat_provider
        try:
            yield
        finally:
            if managed_chat_provider is not None:
                await managed_chat_provider.close()
            if managed_retriever is not None:
                await managed_retriever.close()

    app = FastAPI(
        title="SPB Policy RAG API",
        version=__version__,
        description="国家邮政局政策知识库在线检索与问答服务",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(search_router)
    app.include_router(chat_router)
    return app
