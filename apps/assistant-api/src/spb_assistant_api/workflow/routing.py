from __future__ import annotations

from typing import Literal

from pydantic import TypeAdapter

from ..domain.agent_actions import (
    ClarifyIntentAction,
    CollectSlotsAction,
    HandoffAction,
    InvokeToolAction,
    NextAction,
    RespondAction,
    UnderstandAction,
    ValidateResultAction,
)

from .state import AgentState, SpikeState


SpikeRoute = Literal["clarify", "complete"]


def route_after_understanding(state: SpikeState) -> SpikeRoute:
    phase = state.get("phase")
    if phase == "clarifying":
        return "clarify"
    if phase == "ready" and state.get("mail_no"):
        return "complete"
    raise ValueError(f"理解节点产生了不可路由状态: {phase!r}")


AgentActionRoute = Literal[
    "understand",
    "clarify",
    "execute_tool",
    "validate_result",
    "compose_response",
]
ValidationRoute = Literal["recover", "compose_response"]
_ACTION_ADAPTER = TypeAdapter(NextAction)


def route_next_action(state: AgentState) -> AgentActionRoute:
    action = _ACTION_ADAPTER.validate_python(state.get("pending_action"))
    if isinstance(action, UnderstandAction):
        return "understand"
    if isinstance(action, (ClarifyIntentAction, CollectSlotsAction)):
        return "clarify"
    if isinstance(action, InvokeToolAction):
        return "execute_tool"
    if isinstance(action, ValidateResultAction):
        return "validate_result"
    if isinstance(action, (RespondAction, HandoffAction)):
        return "compose_response"
    raise AssertionError(f"未覆盖的 Agent Action: {type(action).__name__}")


def route_after_validation(state: AgentState) -> ValidationRoute:
    phase = state.get("phase")
    if phase == "recovering":
        return "recover"
    if phase == "responding":
        return "compose_response"
    raise ValueError(f"校验节点产生了不可路由状态: {phase!r}")
