from __future__ import annotations

from collections.abc import Callable, Mapping
from inspect import iscoroutinefunction
from typing import Any

from langgraph.errors import GraphInterrupt

from ..observability.telemetry import OperationObservation, WorkflowTelemetry
from .state import AgentState


def instrument_node(
    name: str, node: Callable[..., Any], telemetry: WorkflowTelemetry | None
) -> Callable[..., Any]:
    """Wrap actual execution, preserving LangGraph's sync/async scheduling."""
    if telemetry is None:
        return node

    def sync_node(state: AgentState):
        with telemetry.measure(node=name) as observation:
            telemetry._best_effort(
                "start", lambda: _observe_attempt(name, state, observation)
            )
            try:
                update = node(state)
            except GraphInterrupt:
                observation.outcome = "interrupted"
                raise
            telemetry._best_effort(
                "finish", lambda: _observe_update(name, update, observation)
            )
            return update

    async def async_node(state: AgentState):
        with telemetry.measure(node=name) as observation:
            telemetry._best_effort(
                "start", lambda: _observe_attempt(name, state, observation)
            )
            try:
                update = await node(state)
            except GraphInterrupt:
                observation.outcome = "interrupted"
                raise
            telemetry._best_effort(
                "finish", lambda: _observe_update(name, update, observation)
            )
            return update

    return async_node if iscoroutinefunction(node) else sync_node


def _observe_attempt(
    name: str, state: AgentState, observation: OperationObservation
) -> None:
    action = state.get("pending_action")
    if name == "execute_tool" and isinstance(action, Mapping):
        attempt = action.get("attempt")
        if type(attempt) is int:
            observation.tool_attempt = attempt


def _observe_update(
    name: str, update: object, observation: OperationObservation
) -> None:
    if not isinstance(update, Mapping):
        return
    failure = update.get("last_error")
    if isinstance(failure, Mapping):
        observation.outcome = "failed"
        observation.failure_category = failure.get("category")
    if name == "execute_tool":
        calls = update.get("tool_calls")
        if (
            isinstance(calls, list)
            and calls
            and isinstance(calls[-1], Mapping)
        ):
            status = calls[-1].get("status")
            if status in {"succeeded", "reused"}:
                observation.tool_reused = status == "reused"
