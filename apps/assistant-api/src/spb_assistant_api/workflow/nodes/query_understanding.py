from __future__ import annotations

from ...domain.agent_events import AgentEventType
from ...domain.intents import Intent
from ...domain.ports import QueryUnderstander
from ..node_utils import agent_event
from ..state import AgentState


def create_understand_node(
    understander: QueryUnderstander,
):
    async def understand(state: AgentState) -> dict[str, object]:
        active_raw = state.get("active_intent")
        explicit_raw = state.get("explicit_intent")
        active = Intent(active_raw) if active_raw else None
        explicit = Intent(explicit_raw) if explicit_raw else None
        result = await understander.understand(
            message=state.get("latest_message", ""),
            active_intent=active,
            explicit_intent=explicit,
        )
        selected = result.selected_intent
        phase = "clarifying"
        if selected is not Intent.UNKNOWN:
            phase = "collecting" if result.missing_slots else "ready"
        return {
            "latest_message": "",
            "active_intent": (
                None if selected is Intent.UNKNOWN else selected.value
            ),
            "slots": (
                result.slots.model_dump(mode="json")
                if result.slots is not None
                else None
            ),
            "missing_slots": result.missing_slots,
            "ambiguities": result.ambiguities,
            "phase": phase,
            "audit_events": [
                agent_event(
                    AgentEventType.QUERY_UNDERSTOOD,
                    node="understand",
                    phase=phase,
                    intent=selected.value,
                    source=result.source,
                    missing_slot_count=len(result.missing_slots),
                    parser_version=result.parser_version,
                )
            ],
        }

    return understand
