from __future__ import annotations

from ..domain.agent_events import AgentEvent, AgentEventType, EventDetail


def agent_event(
    event_type: AgentEventType,
    *,
    node: str,
    phase: str,
    **details: EventDetail,
) -> dict[str, object]:
    return AgentEvent(
        event_type=event_type,
        node=node,
        phase=phase,
        details=details,
    ).model_dump(mode="json")
