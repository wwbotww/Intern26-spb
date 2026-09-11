from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from ...domain.agent_events import AgentEventType
from ...domain.agent_actions import AgentMessageInput
from ..node_utils import agent_event
from ..migrations import CURRENT_AGENT_STATE_SCHEMA
from ..state import AgentState


def ingest_agent_input(state: AgentState) -> dict[str, object]:
    payload = AgentMessageInput(message=state.get("message", ""))
    conversation_id = state.get("conversation_id", "").strip()
    turn_id = state.get("turn_id", "").strip()
    if not conversation_id or not turn_id:
        raise ValueError("Agent 输入缺少 conversation_id 或 turn_id")

    return {
        "schema_version": CURRENT_AGENT_STATE_SCHEMA,
        # The initial turn seeds a query identity once; resumes change turn_id
        # but retain query_id. Deterministic ingest is safe to checkpoint-replay.
        "query_id": str(
            uuid5(NAMESPACE_URL, f"agent-query-v3:{conversation_id}:{turn_id}")
        ),
        "legacy_tool_call": None,
        "price_candidates": None,
        "price_selected_token": None,
        "price_result_slots": [],
        "price_clarification_count": 0,
        "postage_policy_snapshot": None,
        "postage_review_fingerprint": None,
        "postage_confirmed_fingerprint": None,
        "postage_requirements": [],
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
