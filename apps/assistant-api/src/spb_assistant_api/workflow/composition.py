from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from langgraph.checkpoint.base import BaseCheckpointSaver

from ..domain.intents import Intent
from ..domain.ports import TrackingGateway, ToolExecutionRepository
from ..services.agent_tools import (
    AgentCommandDispatcher,
    AgentToolRegistry,
    ToolExecutor,
)
from ..services.query_understanding import RuleBasedQueryUnderstander
from ..services.result_validator import AgentResultValidator
from ..tools.tracking import TrackingTool
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
            understander=RuleBasedQueryUnderstander(),
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
