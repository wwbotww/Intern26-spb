from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.agent_errors import AgentOperationError
from ..domain.failures import AgentFailure, FailureCategory


CURRENT_AGENT_STATE_SCHEMA = "2"


@dataclass(frozen=True, slots=True)
class StateMigrationResult:
    state: dict[str, Any]
    source_version: str
    target_version: str
    changed: bool


class AgentStateMigrator:
    """Pure additive migration gate for persisted JSON-native state."""

    def migrate(
        self,
        raw_state: Mapping[str, Any],
    ) -> StateMigrationResult:
        state = dict(raw_state)
        source = str(state.get("schema_version", "1"))
        if source == CURRENT_AGENT_STATE_SCHEMA:
            return StateMigrationResult(
                state=state,
                source_version=source,
                target_version=source,
                changed=False,
            )
        if source != "1":
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.STATE_SCHEMA_INCOMPATIBLE,
                    code="unsupported_agent_state_schema",
                    message="持久化 Workflow State 版本不受支持",
                )
            )

        state.update(
            {
                "schema_version": CURRENT_AGENT_STATE_SCHEMA,
                "candidate_intents": list(
                    state.get("candidate_intents", [])
                ),
                "multi_intent": bool(state.get("multi_intent", False)),
                "control": str(state.get("control", "none")),
                "slot_provenance": list(
                    state.get("slot_provenance", [])
                ),
                "confirm_slot_overwrite": bool(
                    state.get("confirm_slot_overwrite", False)
                ),
                "intent_choice_confirmed": bool(
                    state.get("intent_choice_confirmed", False)
                ),
                "pending_query": str(state.get("pending_query", "")),
                "understanding_parser_version": str(
                    state.get("understanding_parser_version", "legacy-v1")
                ),
                "understanding_prompt_version": state.get(
                    "understanding_prompt_version"
                ),
            }
        )
        return StateMigrationResult(
            state=state,
            source_version=source,
            target_version=CURRENT_AGENT_STATE_SCHEMA,
            changed=True,
        )
