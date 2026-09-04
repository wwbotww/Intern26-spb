from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver

from ..adapters.checkpointer_factory import create_sqlite_checkpointer
from ..adapters.sqlite_persistence import create_sqlite_agent_repositories
from ..domain.intents import Intent
from ..domain.ports import (
    QueryUnderstander,
    TrackingGateway,
    ToolExecutionRepository,
)
from ..services.agent_tools import (
    AgentCommandDispatcher,
    AgentToolRegistry,
    ToolExecutor,
)
from ..services.query_understanding import HybridQueryUnderstander
from ..services.result_validator import AgentResultValidator
from ..tools.tracking import TrackingTool
from .conversation_service import (
    ConversationJanitor,
    ConversationRunCoordinator,
    StatefulAgentService,
)
from .graph import (
    TrackingAgentGraphDependencies,
    build_tracking_agent_graph,
)
from .policy import WorkflowPolicy
from .runtime import TrackingAgentRuntime


def create_tracking_agent_runtime(
    *,
    checkpointer: BaseCheckpointSaver[str],
    gateway: TrackingGateway,
    receipts: ToolExecutionRepository,
    understander: QueryUnderstander | None = None,
    recursion_limit: int = 24,
    max_steps: int = 8,
    max_tool_calls: int = 1,
    max_retries: int = 1,
    request_timeout_seconds: float = 30,
    clock: Callable[[], datetime] | None = None,
) -> TrackingAgentRuntime:
    """Compose the phase-1 kernel from injected runtime dependencies."""

    resolved_clock = clock or (lambda: datetime.now(UTC))
    registry = AgentToolRegistry(
        [TrackingTool(gateway)],
        required_intents=frozenset({Intent.TRACKING}),
    )
    policy = WorkflowPolicy(registry.descriptors)
    executor = ToolExecutor(
        AgentCommandDispatcher(registry),
        receipts,
        clock=resolved_clock,
    )
    graph = build_tracking_agent_graph(
        checkpointer=checkpointer,
        dependencies=TrackingAgentGraphDependencies(
            understander=understander or HybridQueryUnderstander(),
            policy=policy,
            executor=executor,
            validator=AgentResultValidator(),
        ),
    )
    return TrackingAgentRuntime(
        graph,
        recursion_limit=recursion_limit,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        max_retries=max_retries,
        request_timeout_seconds=request_timeout_seconds,
        clock=resolved_clock,
    )


@dataclass(frozen=True, slots=True)
class PersistentTrackingAgentComponents:
    runtime: TrackingAgentRuntime
    service: StatefulAgentService
    janitor: ConversationJanitor


@asynccontextmanager
async def create_persistent_tracking_agent(
    *,
    database_path: str | Path,
    gateway: TrackingGateway,
    understander: QueryUnderstander | None = None,
    conversation_ttl: timedelta = timedelta(minutes=30),
    recursion_limit: int = 24,
    max_steps: int = 8,
    max_tool_calls: int = 1,
    max_retries: int = 1,
    request_timeout_seconds: float = 30,
    clock: Callable[[], datetime] | None = None,
) -> AsyncIterator[PersistentTrackingAgentComponents]:
    """Compose the local phase-2 SQLite runtime and lifecycle boundary."""

    resolved_clock = clock or (lambda: datetime.now(UTC))
    async with create_sqlite_checkpointer(database_path) as checkpointer:
        async with create_sqlite_agent_repositories(
            database_path
        ) as repositories:
            runtime = create_tracking_agent_runtime(
                checkpointer=checkpointer,
                gateway=gateway,
                receipts=repositories.tool_receipts,
                understander=understander,
                recursion_limit=recursion_limit,
                max_steps=max_steps,
                max_tool_calls=max_tool_calls,
                max_retries=max_retries,
                request_timeout_seconds=request_timeout_seconds,
                clock=resolved_clock,
            )
            coordinator = ConversationRunCoordinator()
            service = StatefulAgentService(
                runtime=runtime,
                metadata=repositories.metadata,
                coordinator=coordinator,
                ttl=conversation_ttl,
                clock=resolved_clock,
            )
            yield PersistentTrackingAgentComponents(
                runtime=runtime,
                service=service,
                janitor=ConversationJanitor(
                    metadata=repositories.metadata,
                    tool_receipts=repositories.tool_receipts,
                    checkpointer=checkpointer,
                    coordinator=coordinator,
                    clock=resolved_clock,
                ),
            )
