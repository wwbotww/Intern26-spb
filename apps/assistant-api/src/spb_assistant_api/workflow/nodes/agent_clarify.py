from __future__ import annotations

from langgraph.types import interrupt

from ...domain.agent_actions import (
    ClarificationRequest,
    CollectSlotsAction,
    TrackingResume,
)
from ...domain.agent_events import AgentEventType
from ...domain.intents import Intent
from ..node_utils import agent_event
from ..state import AgentState


def clarify_agent_input(state: AgentState) -> dict[str, object]:
    action = CollectSlotsAction.model_validate(state.get("pending_action"))
    request = ClarificationRequest(
        intent=action.intent,
        prompt=action.prompt,
        required_inputs=action.required_inputs,
    )
    resumed = interrupt(request.model_dump(mode="json"))
    if action.intent is not Intent.TRACKING:
        raise ValueError("阶段 1 仅实现轨迹补槽")
    payload = TrackingResume.model_validate(resumed)
    update: dict[str, object] = {
        "latest_message": payload.mail_no,
        "explicit_intent": Intent.TRACKING.value,
        "phase": "understanding",
        "turn_count": int(state.get("turn_count", 0)) + 1,
        "step_count": 0,
        "required_inputs": [],
        "audit_events": [
            agent_event(
                AgentEventType.CLARIFICATION_RESUMED,
                node="clarify",
                phase="understanding",
                intent=Intent.TRACKING.value,
            )
        ],
    }
    if payload.turn_id is not None:
        update["turn_id"] = str(payload.turn_id)
    if payload.deadline_at is not None:
        update["deadline_at"] = payload.deadline_at.isoformat()
    return update
