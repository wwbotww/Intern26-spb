from __future__ import annotations

from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..domain.ports import QueryUnderstander
from ..observability.telemetry import WorkflowTelemetry
from ..services.agent_tools import ToolExecutor
from ..services.result_validator import AgentResultValidator
from ..services.postage_preflight import PostagePreflight
from .instrumentation import instrument_node
from .migrations import migrate_node_state
from .nodes import (
    clarify_agent_input,
    clarify_tracking_number,
    complete_spike,
    compose_agent_response,
    create_decide_node,
    create_execute_tool_node,
    create_recover_node,
    create_understand_node,
    create_validate_result_node,
    ingest_agent_input,
    understand_tracking_request,
)
from .policy import WorkflowPolicy
from .routing import (
    route_after_understanding,
    route_after_validation,
    route_next_action,
)
from .state import (
    AgentInputState,
    AgentOutputState,
    AgentState,
    SpikeInputState,
    SpikeOutputState,
    SpikeState,
)


def build_spike_graph(
    *,
    checkpointer: BaseCheckpointSaver[str],
) -> CompiledStateGraph:
    builder = StateGraph(
        SpikeState,
        input_schema=SpikeInputState,
        output_schema=SpikeOutputState,
    )
    builder.add_node("understand", understand_tracking_request)
    builder.add_node("clarify", clarify_tracking_number)
    builder.add_node("complete", complete_spike)

    builder.add_edge(START, "understand")
    builder.add_conditional_edges(
        "understand",
        route_after_understanding,
        {
            "clarify": "clarify",
            "complete": "complete",
        },
    )
    builder.add_edge("clarify", "understand")
    builder.add_edge("complete", END)
    return builder.compile(
        checkpointer=checkpointer,
        name="assistant-agent-phase-0-spike",
    )


@dataclass(frozen=True, slots=True)
class AgentGraphDependencies:
    understander: QueryUnderstander
    policy: WorkflowPolicy
    executor: ToolExecutor
    validator: AgentResultValidator
    postage_preflight: PostagePreflight | None = None


def build_agent_graph(
    *,
    checkpointer: BaseCheckpointSaver[str],
    dependencies: AgentGraphDependencies,
    telemetry: WorkflowTelemetry | None = None,
) -> CompiledStateGraph:
    builder = StateGraph(
        AgentState,
        input_schema=AgentInputState,
        output_schema=AgentOutputState,
    )
    nodes = {
        "ingest": ingest_agent_input,
        "understand": create_understand_node(dependencies.understander, postage_preflight=dependencies.postage_preflight),
        "decide_next": create_decide_node(dependencies.policy),
        "clarify": clarify_agent_input,
        "execute_tool": create_execute_tool_node(dependencies.executor),
        "validate_result": create_validate_result_node(dependencies.validator),
        "recover": create_recover_node(dependencies.policy),
        "compose_response": compose_agent_response,
    }
    for name, node in nodes.items():
        builder.add_node(
            name, instrument_node(name, migrate_node_state(node), telemetry)
        )

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "understand")
    builder.add_edge("understand", "decide_next")
    builder.add_conditional_edges(
        "decide_next",
        route_next_action,
        {
            "understand": "understand",
            "clarify": "clarify",
            "execute_tool": "execute_tool",
            "validate_result": "validate_result",
            "compose_response": "compose_response",
        },
    )
    builder.add_edge("clarify", "understand")
    builder.add_edge("execute_tool", "validate_result")
    builder.add_conditional_edges(
        "validate_result",
        route_after_validation,
        {
            "recover": "recover",
            "compose_response": "compose_response",
        },
    )
    builder.add_edge("recover", "decide_next")
    builder.add_edge("compose_response", END)
    return builder.compile(
        checkpointer=checkpointer,
        name="assistant-agent-v2-kernel",
    )


# Phase 1 compatibility aliases for persisted callers and older tests.
TrackingAgentGraphDependencies = AgentGraphDependencies
build_tracking_agent_graph = build_agent_graph
