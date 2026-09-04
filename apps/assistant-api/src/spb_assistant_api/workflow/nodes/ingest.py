from __future__ import annotations

from ...domain.agent_events import AgentEventType
from ...domain.agent_actions import AgentMessageInput
from ..node_utils import agent_event
from ..state import AgentState


def ingest_agent_input(state: AgentState) -> dict[str, object]:
    payload = AgentMessageInput(message=state.get("message", ""))
    conversation_id = state.get("conversation_id", "").strip()
    turn_id = state.get("turn_id", "").strip()
    if not conversation_id or not turn_id:
        raise ValueError("Agent 输入缺少 conversation_id 或 turn_id")

    return {
        "schema_version": "2",
        "latest_message": payload.message,
        "message": "",
        "phase": "understanding",
        "turn_count": int(state.get("turn_count", 0)) + 1,
        "active_intent": None,
        "candidate_intents": [],
        "multi_intent": False,
        "control": "none",
        "slots": None,
        "slot_provenance": [],
        "confirm_slot_overwrite": False,
        "intent_choice_confirmed": False,
        "missing_slots": [],
        "ambiguities": [],
        "pending_query": "",
        "understanding_parser_version": "",
        "understanding_prompt_version": None,
        "pending_action": None,
        "last_result": None,
        "last_error": None,
        "result": None,
        "failure": None,
        "tool_call_count": 0,
        "retry_count": 0,
        "step_count": 0,
        "required_inputs": [],
        "reply": "",
        "warnings": [],
        "finish_reason": None,
        "audit_events": [
            agent_event(
                AgentEventType.CONVERSATION_STARTED,
                node="ingest",
                phase="understanding",
            ),
            agent_event(
                AgentEventType.USER_MESSAGE_RECEIVED,
                node="ingest",
                phase="understanding",
            ),
        ],
    }
