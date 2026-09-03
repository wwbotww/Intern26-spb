from __future__ import annotations

from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..domain.ports import QueryUnderstander
from ..services.agent_tools import ToolExecutor
from ..services.result_validator import AgentResultValidator
from .nodes import (
    clarify_agent_input,
    clarify_tracking_number,
    compose_agent_response,
    complete_spike,
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
class TrackingAgentGraphDependencies:
    understander: QueryUnderstander
    policy: WorkflowPolicy
    executor: ToolExecutor
    validator: AgentResultValidator


def build_tracking_agent_graph(
    *,
    checkpointer: BaseCheckpointSaver[str],
    dependencies: TrackingAgentGraphDependencies,
) -> CompiledStateGraph:
    builder = StateGraph(
        AgentState,
        input_schema=AgentInputState,
        output_schema=AgentOutputState,
    )
    builder.add_node("ingest", ingest_agent_input)
    builder.add_node(
        "understand",
        create_understand_node(dependencies.understander),
    )
    builder.add_node(
        "decide_next",
        create_decide_node(dependencies.policy),
    )
    builder.add_node("clarify", clarify_agent_input)
    builder.add_node(
        "execute_tool",
        create_execute_tool_node(dependencies.executor),
    )
    builder.add_node(
        "validate_result",
        create_validate_result_node(dependencies.validator),
    )
    builder.add_node(
        "recover",
        create_recover_node(dependencies.policy),
    )
    builder.add_node("compose_response", compose_agent_response)

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
        name="assistant-agent-phase-1-tracking",
    )
