from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from ..adapters.checkpointer_factory import create_sqlite_checkpointer
from ..adapters.legacy_agent_tools import (
    DevicePriceAssistantToolAdapter,
    PolicyAssistantToolAdapter,
)
from ..adapters.sqlite_persistence import (
    SqliteAgentReadinessProbe,
    create_sqlite_agent_repositories,
)
from ..domain.ports import (
    AgentTool,
    AssistantTool,
    DeliveryTimeGateway,
    PostageGateway,
    QueryUnderstander,
    TrackingGateway,
    ToolExecutionRepository,
)
from ..observability.agent_trace import log_agent_workflow_trace
from ..observability.telemetry import WorkflowTelemetry
from ..services.agent_tools import (
    AgentCommandDispatcher,
    AgentToolRegistry,
    ToolExecutor,
)
from ..services.query_understanding import HybridQueryUnderstander
from ..services.result_validator import AgentResultValidator
from ..services.postage_preflight import PostagePreflight
from ..tools.delivery_time import DeliveryTimeTool
from ..tools.postage import PostageTool
from ..tools.tracking import TrackingTool
from ..tools.product_price import ProductPriceTool
from ..services.product_price_query import ProductPriceQueryService
from ..services.query_understanding import RuleBasedQueryUnderstander
from .conversation_service import (
    ConversationJanitor,
    ConversationRunCoordinator,
    StatefulAgentService,
)
from .graph import (
    AgentGraphDependencies,
    build_agent_graph,
)
from .policy import WorkflowPolicy
from .runtime import StatefulAgentRuntime
from .tracing import WorkflowTraceSink


def create_agent_runtime(
    *,
    checkpointer: BaseCheckpointSaver[str],
    receipts: ToolExecutionRepository,
    tracking_gateway: TrackingGateway | None = None,
    delivery_time_gateway: DeliveryTimeGateway | None = None,
    postage_gateway: PostageGateway | None = None,
    postage_preflight: PostagePreflight | None = None,
    policy_tool: AssistantTool | None = None,
    device_price_tool: AssistantTool | None = None,
    product_price_service: ProductPriceQueryService | None = None,
    understander: QueryUnderstander | None = None,
    recursion_limit: int = 24,
    max_steps: int = 8,
    max_tool_calls: int = 1,
    max_retries: int = 1,
    request_timeout_seconds: float = 30,
    clock: Callable[[], datetime] | None = None,
    workflow_trace_sink: WorkflowTraceSink | None = log_agent_workflow_trace,
    telemetry: WorkflowTelemetry | None = None,
) -> StatefulAgentRuntime:
    """Compose the capability-neutral Agent from injected dependencies.

    Injected V1 policy/device tools are borrowed: their lifecycle remains with
    the V1 registry so one initialized instance can safely serve both APIs.
    """

    resolved_clock = clock or (lambda: datetime.now(UTC))
    tools: list[AgentTool] = []
    if product_price_service is not None:
        if device_price_tool is not None:
            raise ValueError("不能同时装配新旧价格 Agent 意图")
        tools.append(ProductPriceTool(product_price_service))
    if tracking_gateway is not None:
        tools.append(TrackingTool(tracking_gateway))
    if delivery_time_gateway is not None:
        tools.append(DeliveryTimeTool(delivery_time_gateway))
    if postage_gateway is not None:
        tools.append(PostageTool(postage_gateway, preflight=postage_preflight))
    if policy_tool is not None:
        tools.append(PolicyAssistantToolAdapter(policy_tool))
    if device_price_tool is not None:
        tools.append(DevicePriceAssistantToolAdapter(device_price_tool))
    registry = AgentToolRegistry(tools)
    policy = WorkflowPolicy(registry.descriptors, postage_preflight=postage_preflight, clock=resolved_clock)
    executor = ToolExecutor(
        AgentCommandDispatcher(registry),
        receipts,
        clock=resolved_clock,
    )
    graph = build_agent_graph(
        checkpointer=checkpointer,
        telemetry=telemetry,
        dependencies=AgentGraphDependencies(
            understander=understander or HybridQueryUnderstander(
                rules=RuleBasedQueryUnderstander(product_price_enabled=product_price_service is not None)),
            policy=policy,
            executor=executor,
            validator=AgentResultValidator(),
            postage_preflight=postage_preflight,
            price_clock=resolved_clock,
        ),
    )
    return StatefulAgentRuntime(
        graph,
        recursion_limit=recursion_limit,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        max_retries=max_retries,
        request_timeout_seconds=request_timeout_seconds,
        capability_descriptors=registry.descriptors,
        clock=resolved_clock,
        workflow_trace_sink=workflow_trace_sink,
        telemetry=telemetry,
    )


@dataclass(frozen=True, slots=True)
class PersistentAgentComponents:
    runtime: StatefulAgentRuntime
    service: StatefulAgentService
    janitor: ConversationJanitor
    readiness: SqliteAgentReadinessProbe


@asynccontextmanager
async def create_persistent_agent(
    *,
    database_path: str | Path,
    managed_storage: bool = False,
    tracking_gateway: TrackingGateway | None = None,
    delivery_time_gateway: DeliveryTimeGateway | None = None,
    postage_gateway: PostageGateway | None = None,
    postage_preflight: PostagePreflight | None = None,
    policy_tool: AssistantTool | None = None,
    device_price_tool: AssistantTool | None = None,
    product_price_service: ProductPriceQueryService | None = None,
    understander: QueryUnderstander | None = None,
    conversation_ttl: timedelta = timedelta(minutes=30),
    recursion_limit: int = 24,
    max_steps: int = 8,
    max_tool_calls: int = 1,
    max_retries: int = 1,
    request_timeout_seconds: float = 30,
    janitor_batch_size: int = 100,
    clock: Callable[[], datetime] | None = None,
    workflow_trace_sink: WorkflowTraceSink | None = log_agent_workflow_trace,
    telemetry: WorkflowTelemetry | None = None,
) -> AsyncIterator[PersistentAgentComponents]:
    """Compose the local SQLite Agent runtime and persistence lifecycle.

    This context owns SQLite resources, but any borrowed V1 tools are expected
    to be initialized and closed by their original composition root.
    """

    resolved_clock = clock or (lambda: datetime.now(UTC))
    async with create_sqlite_checkpointer(database_path, managed_storage=managed_storage) as checkpointer:
        async with create_sqlite_agent_repositories(
            database_path
        ) as repositories:
            runtime = create_agent_runtime(
                checkpointer=checkpointer,
                receipts=repositories.tool_receipts,
                tracking_gateway=tracking_gateway,
                delivery_time_gateway=delivery_time_gateway,
                postage_gateway=postage_gateway,
                postage_preflight=postage_preflight,
                policy_tool=policy_tool,
                device_price_tool=device_price_tool,
                product_price_service=product_price_service,
                understander=understander,
                recursion_limit=recursion_limit,
                max_steps=max_steps,
                max_tool_calls=max_tool_calls,
                max_retries=max_retries,
                request_timeout_seconds=request_timeout_seconds,
                clock=resolved_clock,
                workflow_trace_sink=workflow_trace_sink,
                telemetry=telemetry,
            )
            coordinator = ConversationRunCoordinator()
            service = StatefulAgentService(
                runtime=runtime,
                metadata=repositories.metadata,
                tool_receipts=repositories.tool_receipts,
                checkpointer=checkpointer,
                coordinator=coordinator,
                ttl=conversation_ttl,
                clock=resolved_clock,
            )
            yield PersistentAgentComponents(
                runtime=runtime,
                service=service,
                janitor=ConversationJanitor(
                    metadata=repositories.metadata,
                    tool_receipts=repositories.tool_receipts,
                    checkpointer=checkpointer,
                    coordinator=coordinator,
                    clock=resolved_clock,
                    batch_size=janitor_batch_size,
                ),
                readiness=repositories.readiness,
            )


# Compatibility surface retained while Phase 1/2 callers migrate.
PersistentTrackingAgentComponents = PersistentAgentComponents


def create_tracking_agent_runtime(
    *,
    gateway: TrackingGateway,
    **kwargs: Any,
) -> StatefulAgentRuntime:
    return create_agent_runtime(
        tracking_gateway=gateway,
        **kwargs,
    )


@asynccontextmanager
async def create_persistent_tracking_agent(
    *,
    database_path: str | Path,
    gateway: TrackingGateway,
    **kwargs: Any,
) -> AsyncIterator[PersistentAgentComponents]:
    async with create_persistent_agent(
        database_path=database_path,
        tracking_gateway=gateway,
        **kwargs,
    ) as components:
        yield components
