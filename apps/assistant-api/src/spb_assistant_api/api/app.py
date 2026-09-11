from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from types import TracebackType

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .. import __version__
from ..adapters.mysql_price import MySQLPriceRepository
from ..adapters.rag_policy import RagPolicyClient
from ..configured_agent import configured_tracking, create_configured_agent_factory
from ..configured_price import configured_product_price
from ..domain.models import QueryMode
from ..domain.ports import AssistantTool, ProductPriceReadRepository
from ..middleware.operations import OperationsConfig, OperationsMiddleware
from ..observability.metrics import ServiceMetrics
from ..services.dispatcher import (
    DEVICE_PRICE_TOOL_NAME,
    POLICY_TOOL_NAME,
    QueryDispatcher,
    ToolRegistry,
)
from ..services.agent_operations import AgentJanitorScheduler
from ..settings import AssistantSettings
from ..tools.device_price import DevicePriceTool
from ..tools.policy import PolicyKnowledgeTool
from ..tools.unavailable import UnavailableTool
from .agent_contracts import AgentApiDependencies, AgentApiDependencyFactory
from .routes.agent import router as agent_router
from .routes.chat import router as chat_router
from .routes.health import router as health_router
from .routes.metrics import router as metrics_router
from .routes.browser_session import router as browser_session_router


logger = logging.getLogger(__name__)


async def _close_owned_resource(
    close: Callable[[], Awaitable[None]],
    exc_type: type[BaseException] | None,
    error: BaseException | None,
    traceback: TracebackType | None,
) -> bool:
    try:
        await close()
    except Exception:
        if error is None:
            raise
        # Preserve the startup/request error. Do not log exception text that
        # might contain a dependency URL or database connection information.
        logger.error("owned dependency cleanup failed after lifecycle error")
    return False


def _default_tools(
    settings: AssistantSettings,
    *,
    product_price_tool: AssistantTool | None = None,
) -> dict[QueryMode, AssistantTool]:
    if product_price_tool is not None and settings.price_data_model != "catalog_v2":
        raise ValueError("V2 价格 Tool 必须显式选择 catalog_v2")
    tools: dict[QueryMode, AssistantTool] = {
        QueryMode.POLICY: UnavailableTool(POLICY_TOOL_NAME),
        QueryMode.DEVICE_PRICE: UnavailableTool(DEVICE_PRICE_TOOL_NAME),
    }
    rag_base_url = settings.rag_base_url.strip()
    rag_api_key = settings.rag_api_key.get_secret_value().strip()
    if rag_base_url and rag_api_key:
        policy_source = RagPolicyClient(
            base_url=rag_base_url,
            api_key=rag_api_key,
            timeout_seconds=settings.rag_timeout_seconds,
            health_timeout_seconds=settings.rag_health_timeout_seconds,
            top_k=settings.rag_top_k,
            candidate_k=settings.rag_candidate_k,
            max_connections=settings.max_concurrency,
            verify_tls=settings.rag_verify_tls,
        )
        tools[QueryMode.POLICY] = PolicyKnowledgeTool(
            source=policy_source
        )
    mysql_dsn = settings.mysql_dsn.get_secret_value().strip()
    if mysql_dsn and settings.price_data_model == "device_v1":
        repository = MySQLPriceRepository(
            dsn=mysql_dsn,
            pool_size=settings.mysql_pool_size,
            connect_timeout_seconds=(
                settings.mysql_connect_timeout_seconds
            ),
            query_timeout_seconds=(
                settings.mysql_query_timeout_seconds
            ),
        )
        tools[QueryMode.DEVICE_PRICE] = DevicePriceTool(
            repository=repository,
            candidate_limit=settings.price_candidate_limit,
            result_limit=settings.price_result_limit,
            match_threshold=settings.price_match_threshold,
        )
    if product_price_tool is not None:
        # This wrapper borrows the application-owned V2 query service.
        tools[QueryMode.DEVICE_PRICE] = product_price_tool
    return tools


def create_app(
    *,
    settings: AssistantSettings | None = None,
    tools: Mapping[QueryMode, AssistantTool] | None = None,
    agent_api: AgentApiDependencies | None = None,
    agent_api_factory: AgentApiDependencyFactory | None = None,
    metrics: ServiceMetrics | None = None,
    product_price_repository: ProductPriceReadRepository | None = None,
) -> FastAPI:
    if agent_api is not None and agent_api_factory is not None:
        raise ValueError("agent_api 与 agent_api_factory 不能同时提供")
    resolved_settings = settings or AssistantSettings()
    if tools is not None and product_price_repository is not None:
        raise ValueError("tools 与 product_price_repository 不能同时提供")
    if resolved_settings.agent_enabled:
        configured_tracking(resolved_settings)
    service_metrics = metrics if metrics is not None else ServiceMetrics()
    product_price = (
        configured_product_price(
            resolved_settings, repository=product_price_repository,
        )
        if tools is None else None
    )
    resolved_tools = (
        tools
        if tools is not None
        else _default_tools(
            resolved_settings,
            product_price_tool=product_price.tool if product_price is not None else None,
        )
    )
    registry = ToolRegistry(resolved_tools)
    dispatcher = QueryDispatcher(registry)
    if (
        resolved_settings.agent_enabled
        and agent_api is None
        and agent_api_factory is None
    ):
        agent_api_factory = create_configured_agent_factory(
            settings=resolved_settings,
            legacy_tools=resolved_tools,
            metrics=service_metrics,
            product_price_service=product_price.service if product_price is not None else None,
        )

    @asynccontextmanager
    async def activate_agent_api(
        app: FastAPI,
        dependencies: AgentApiDependencies,
    ) -> AsyncIterator[None]:
        scheduler: AgentJanitorScheduler | None = None
        app.state.agent_api = dependencies
        if dependencies.janitor is not None:
            scheduler = AgentJanitorScheduler(
                janitor=dependencies.janitor,
                metrics=service_metrics,
                interval_seconds=dependencies.janitor_interval_seconds,
                timeout_seconds=dependencies.janitor_timeout_seconds,
            )
        app.state.agent_janitor_scheduler = scheduler
        try:
            if scheduler is not None:
                await scheduler.start()
            yield
        finally:
            if scheduler is not None:
                await scheduler.close()
            app.state.agent_janitor_scheduler = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with AsyncExitStack() as stack:
                if product_price is not None:
                    # Register cleanup before startup, including partial init.
                    stack.push_async_exit(partial(_close_owned_resource, product_price.repository.close))
                stack.push_async_exit(partial(_close_owned_resource, registry.close))
                # Policy clients are allocated while composing tools. Their
                # cleanup must be registered even if price startup fails first.
                if product_price is not None:
                    await product_price.repository.initialize()
                await registry.initialize()
                if agent_api_factory is not None:
                    dependencies = await stack.enter_async_context(agent_api_factory())
                    async with activate_agent_api(app, dependencies):
                        yield
                elif agent_api is not None:
                    async with activate_agent_api(app, agent_api):
                        yield
                else:
                    yield
        finally:
            if agent_api_factory is not None:
                app.state.agent_api = None

    app = FastAPI(
        title="China Post Claims Assistant API",
        version=__version__,
        description="中国邮政理赔助手显式查询模式分发服务",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.metrics = service_metrics
    app.state.registry = registry
    app.state.dispatcher = dispatcher
    app.state.agent_api = agent_api
    app.state.agent_janitor_scheduler = None
    app.state.capacity = asyncio.Semaphore(
        resolved_settings.max_concurrency
    )
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
            browser_session=resolved_settings.browser_session_config(),
        ),
        metrics=service_metrics,
    )
    app.include_router(health_router)
    app.include_router(chat_router)
    if agent_api is not None or agent_api_factory is not None:
        @app.exception_handler(RequestValidationError)
        async def agent_request_validation_handler(
            request: Request,
            error: RequestValidationError,
        ):
            if not request.url.path.startswith("/v2/agent"):
                return await request_validation_exception_handler(
                    request,
                    error,
                )
            return JSONResponse(
                status_code=422,
                content={
                    "detail": {
                        "code": "invalid_agent_request",
                        "message": "Agent 请求未通过 schema 校验",
                        "request_id": str(
                            getattr(request.state, "request_id", "")
                        ),
                        "category": "invalid_input",
                        "retryable": False,
                    }
                },
            )

        app.include_router(agent_router)
        if resolved_settings.agent_browser_session_enabled:
            app.include_router(browser_session_router)
    if resolved_settings.metrics_enabled:
        app.include_router(metrics_router)
    return app
