from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .. import __version__
from ..adapters.deepseek import (
    DeepSeekChatProvider,
    DeepSeekConfig,
    DeepSeekJudgeConfig,
    DeepSeekRelevanceJudge,
)
from ..adapters.embedding import SentenceTransformerQueryEmbedder
from ..adapters.milvus import MilvusHybridSearchStore, MilvusReadConfig
from ..adapters.reranker import TransformerReranker
from ..domain.ports import ChatProvider, RelevanceJudge, Retriever
from ..middleware.operations import OperationsConfig, OperationsMiddleware
from ..observability.metrics import ServiceMetrics
from ..services.reranking import RerankingRetriever
from ..services.search import HybridSearchService
from ..settings import ApiSettings
from .routes.chat import router as chat_router
from .routes.health import router as health_router
from .routes.metrics import router as metrics_router
from .routes.search import router as search_router


logger = logging.getLogger(__name__)


def _build_retriever(
    settings: ApiSettings,
    metrics: ServiceMetrics,
) -> Retriever:
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
    retriever: Retriever = HybridSearchService(
        embedder=embedder,
        store=store,
    )
    if settings.rerank_enabled:
        retriever = RerankingRetriever(
            retriever=retriever,
            reranker=TransformerReranker(
                model_name=settings.rerank_model,
                device=settings.rerank_device,
                batch_size=settings.rerank_batch_size,
                max_length=settings.rerank_max_length,
                max_concurrency=settings.rerank_max_concurrency,
            ),
            fetch_k=settings.rerank_fetch_k,
            min_score=settings.rerank_min_score,
            shadow_mode=settings.rerank_shadow_mode,
            metrics=metrics,
        )
    return retriever


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


def _build_relevance_judge(
    settings: ApiSettings,
) -> DeepSeekRelevanceJudge:
    return DeepSeekRelevanceJudge(
        DeepSeekJudgeConfig(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key.get_secret_value(),
            model=settings.deepseek_model,
            timeout_seconds=settings.deepseek_timeout_seconds,
            max_sources=settings.relevance_judge_max_sources,
            source_max_chars=(
                settings.relevance_judge_source_max_chars
            ),
            max_tokens=settings.relevance_judge_max_tokens,
            attempts=settings.relevance_judge_attempts,
        )
    )


def create_app(
    *,
    settings: ApiSettings | None = None,
    retriever: Retriever | None = None,
    chat_provider: ChatProvider | None = None,
    relevance_judge: RelevanceJudge | None = None,
) -> FastAPI:
    resolved_settings = settings or ApiSettings()
    service_metrics = ServiceMetrics()
    managed_retriever: Retriever | None = None
    managed_chat_provider: ChatProvider | None = None
    managed_relevance_judge: RelevanceJudge | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal managed_chat_provider
        nonlocal managed_relevance_judge
        nonlocal managed_retriever
        app.state.settings = resolved_settings
        app.state.metrics = service_metrics
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
            managed_retriever = _build_retriever(
                resolved_settings,
                service_metrics,
            )
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
        app.state.relevance_judge_initialization_error = ""
        if not resolved_settings.relevance_judge_enabled:
            app.state.relevance_judge = None
            app.state.relevance_judge_initialization_error = "disabled"
        elif relevance_judge is not None:
            app.state.relevance_judge = relevance_judge
        elif not resolved_settings.deepseek_api_key.get_secret_value():
            app.state.relevance_judge = None
            app.state.relevance_judge_initialization_error = (
                "deepseek_not_configured"
            )
        else:
            managed_relevance_judge = _build_relevance_judge(
                resolved_settings
            )
            app.state.relevance_judge = managed_relevance_judge
        try:
            yield
        finally:
            if managed_relevance_judge is not None:
                await managed_relevance_judge.close()
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
    app.state.settings = resolved_settings
    app.state.metrics = service_metrics
    app.add_middleware(
        OperationsMiddleware,
        config=OperationsConfig(
            auth_enabled=resolved_settings.auth_enabled,
            api_keys=resolved_settings.parsed_api_keys(),
            rate_limit_enabled=resolved_settings.rate_limit_enabled,
            rate_limit_requests=resolved_settings.rate_limit_requests,
            rate_limit_window_seconds=(
                resolved_settings.rate_limit_window_seconds
            ),
            max_request_body_bytes=(
                resolved_settings.max_request_body_bytes
            ),
        ),
        metrics=service_metrics,
    )
    app.include_router(health_router)
    app.include_router(search_router)
    app.include_router(chat_router)
    if resolved_settings.metrics_enabled:
        app.include_router(metrics_router)
    return app
