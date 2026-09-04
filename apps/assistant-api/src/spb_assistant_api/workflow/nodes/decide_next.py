from __future__ import annotations

from ...domain.agent_actions import (
    ClarifyIntentAction,
    CollectSlotsAction,
    ControlAction,
    HandoffAction,
    InvokeToolAction,
)
from ...domain.agent_events import AgentEventType
from ..node_utils import agent_event
from ..policy import WorkflowPolicy
from ..state import AgentState


def create_decide_node(policy: WorkflowPolicy):
    def decide_next(state: AgentState) -> dict[str, object]:
        step_count = int(state.get("step_count", 0)) + 1
        decision = policy.decide({**state, "step_count": step_count})
        action = decision.action
        phase = "responding"
        reply = ""
        required_inputs: list[dict[str, object]] = []
        if isinstance(action, CollectSlotsAction):
            phase = "waiting_user"
            reply = action.prompt
            required_inputs = [
                item.model_dump(mode="json")
                for item in action.required_inputs
            ]
        elif isinstance(action, ClarifyIntentAction):
            phase = "waiting_user"
            reply = action.prompt
            required_inputs = [
                {
                    "name": "intent",
                    "label": "查询类型",
                    "type": "choice",
                    "validation_hint": "请选择一个查询目标",
                    "choices": [item.value for item in action.candidates],
                }
            ]
        elif isinstance(action, InvokeToolAction):
            phase = "ready"
        elif isinstance(action, HandoffAction):
            phase = "handoff"
        elif isinstance(action, ControlAction):
            phase = "responding"

        events = [
            agent_event(
                AgentEventType.ACTION_DECIDED,
                node="decide_next",
                phase=phase,
                action=action.type,
                step=step_count,
            )
        ]
        if isinstance(action, (CollectSlotsAction, ClarifyIntentAction)):
            events.append(
                agent_event(
                    AgentEventType.CLARIFICATION_REQUESTED,
                    node="decide_next",
                    phase=phase,
                    intent=(
                        action.intent.value
                        if isinstance(action, CollectSlotsAction)
                        else None
                    ),
                    clarification_type=action.type,
                )
            )
        update: dict[str, object] = {
            "step_count": step_count,
            "pending_action": action.model_dump(mode="json"),
            "phase": phase,
            "reply": reply,
            "required_inputs": required_inputs,
            "audit_events": events,
        }
        if decision.failure is not None:
            failure = decision.failure.model_dump(mode="json")
            update["last_error"] = failure
            update["failure"] = failure
        return update

    return decide_next
