from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from langgraph.types import interrupt

from ...domain.agent_actions import (
    AgentResumeInput,
    ClarificationRequest,
    ClarifyIntentAction,
    CollectSlotsAction,
    IntentClarificationRequest,
    TrackingResume,
)
from ...domain.agent_events import AgentEventType
from ...domain.intents import Intent
from ..node_utils import agent_event
from ..state import AgentState


def clarify_agent_input(state: AgentState) -> dict[str, object]:
    raw_action = state.get("pending_action") or {}
    action_type = raw_action.get("type")
    if action_type == "collect_slots":
        action = CollectSlotsAction.model_validate(raw_action)
        request = ClarificationRequest(
            intent=action.intent,
            prompt=action.prompt,
            required_inputs=action.required_inputs,
        )
        resumed = interrupt(request.model_dump(mode="json"))
        message, turn_id, deadline_at, confirm_overwrite = _slot_resume(
            resumed,
            action.intent,
        )
        return _resume_update(
            state,
            message=message,
            explicit_intent=None,
            event_intent=action.intent,
            turn_id=turn_id,
            deadline_at=deadline_at,
            confirm_overwrite=confirm_overwrite,
            intent_confirmed=False,
        )

    if action_type == "clarify_intent":
        action = ClarifyIntentAction.model_validate(raw_action)
        request = IntentClarificationRequest(
            prompt=action.prompt,
            candidates=action.candidates,
        )
        resumed = interrupt(request.model_dump(mode="json"))
        payload = AgentResumeInput.model_validate(resumed)
        selected = payload.selected_intent
        if selected is None or selected not in action.candidates:
            raise ValueError("恢复值必须选择候选意图之一")
        message = payload.message or state.get("pending_query", "")
        if not message:
            message = selected.value
        return _resume_update(
            state,
            message=message,
            explicit_intent=selected,
            event_intent=selected,
            turn_id=payload.turn_id,
            deadline_at=payload.deadline_at,
            confirm_overwrite=payload.confirm_overwrite,
            intent_confirmed=True,
        )

    raise ValueError(f"澄清节点收到不支持的动作: {action_type!r}")


def _slot_resume(
    resumed: object,
    intent: Intent,
) -> tuple[str, UUID | None, datetime | None, bool]:
    if (
        intent is Intent.TRACKING
        and isinstance(resumed, Mapping)
        and "mail_no" in resumed
    ):
        payload = TrackingResume.model_validate(resumed)
        return (
            payload.mail_no,
            payload.turn_id,
            payload.deadline_at,
            False,
        )
    payload = AgentResumeInput.model_validate(resumed)
    if payload.message is None:
        raise ValueError("补槽恢复必须包含 message")
    return (
        payload.message,
        payload.turn_id,
        payload.deadline_at,
        payload.confirm_overwrite,
    )


def _resume_update(
    state: AgentState,
    *,
    message: str,
    explicit_intent: Intent | None,
    event_intent: Intent,
    turn_id: UUID | None,
    deadline_at: datetime | None,
    confirm_overwrite: bool,
    intent_confirmed: bool,
) -> dict[str, object]:
    events = [
        agent_event(
            AgentEventType.CLARIFICATION_RESUMED,
            node="clarify",
            phase="understanding",
            intent=event_intent.value,
        )
    ]
    if intent_confirmed:
        events.append(
            agent_event(
                AgentEventType.INTENT_CONFIRMED,
                node="clarify",
                phase="understanding",
                intent=event_intent.value,
            )
        )
    update: dict[str, object] = {
        "schema_version": "2",
        "latest_message": message,
        "explicit_intent": (
            explicit_intent.value if explicit_intent is not None else None
        ),
        "confirm_slot_overwrite": confirm_overwrite,
        "phase": "understanding",
        "turn_count": int(state.get("turn_count", 0)) + 1,
        "step_count": 0,
        "required_inputs": [],
        "multi_intent": False,
        "intent_choice_confirmed": intent_confirmed,
        "ambiguities": [],
        "audit_events": events,
    }
    if turn_id is not None:
        update["turn_id"] = str(turn_id)
    if deadline_at is not None:
        update["deadline_at"] = deadline_at.isoformat()
    return update
