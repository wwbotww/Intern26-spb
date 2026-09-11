from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from ..domain.agent_actions import InvokeToolAction
from ..domain.agent_errors import AgentOperationError
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.tooling import LegacyToolCallReference, argument_fingerprint
from .state import AgentState


CURRENT_AGENT_STATE_SCHEMA = "4"


def _incompatible(code: str) -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(
            category=FailureCategory.STATE_SCHEMA_INCOMPATIBLE,
            code=code,
            message="持久化 Workflow State 版本或执行身份不受支持",
        )
    )


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
        # Never reinterpret an interrupted legacy device query as a catalog query.
        # Completed facts/receipts keep their original result type and data.
        if (
            source in {"1", "2", "3"}
            and state.get("active_intent") in {"device_price", "product_price"}
            and state.get("phase") not in {"completed", "failed", "handoff"}
        ):
            raise _incompatible("price_query_restart_required")
        if source in {"3", CURRENT_AGENT_STATE_SCHEMA}:
            try:
                UUID(str(state.get("query_id", "")))
                if state.get("legacy_tool_call") is not None:
                    LegacyToolCallReference.model_validate(state["legacy_tool_call"])
            except (ValueError, TypeError):
                raise _incompatible("query_execution_scope_invalid") from None
            state["schema_version"] = CURRENT_AGENT_STATE_SCHEMA
            return StateMigrationResult(
                state=state,
                source_version=source,
                target_version=CURRENT_AGENT_STATE_SCHEMA,
                changed=source != CURRENT_AGENT_STATE_SCHEMA,
            )
        if source not in {"1", "2"}:
            raise _incompatible("unsupported_agent_state_schema")

        state.update(
            {
                "schema_version": CURRENT_AGENT_STATE_SCHEMA,
                "candidate_intents": list(state.get("candidate_intents", [])),
                "multi_intent": bool(state.get("multi_intent", False)),
                "control": str(state.get("control", "none")),
                "slot_provenance": list(state.get("slot_provenance", [])),
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
        state["query_id"] = str(
            uuid5(
                NAMESPACE_URL,
                f"legacy-query-v3:{state.get('conversation_id', '')}:"
                f"{state.get('turn_id', '')}",
            )
        )
        state["legacy_tool_call"] = None
        pending = state.get("pending_action")
        if isinstance(pending, Mapping) and pending.get("type") == "invoke_tool":
            try:
                action = InvokeToolAction.model_validate(pending)
                fingerprint = argument_fingerprint(action.command)
                expected_id = uuid5(
                    NAMESPACE_URL,
                    f"{state.get('conversation_id', '')}:{fingerprint}",
                )
                if (
                    action.argument_fingerprint != fingerprint
                    or action.tool_call_id != expected_id
                ):
                    raise ValueError("legacy invocation identity mismatch")
                state["legacy_tool_call"] = LegacyToolCallReference(
                    tool_call_id=action.tool_call_id,
                    tool_name=action.tool_name,
                    argument_fingerprint=fingerprint,
                ).model_dump(mode="json")
            except (TypeError, ValueError):
                raise _incompatible("legacy_execution_identity_invalid") from None
        return StateMigrationResult(
            state=state,
            source_version=source,
            target_version=CURRENT_AGENT_STATE_SCHEMA,
            changed=True,
        )


def migrate_node_state(node: Callable[..., Any]) -> Callable[..., Any]:
    """Apply additive migrations at execution, without rewriting interrupts.

    No graph node names/scheduling change, no extra checkpoint or external call.
    Deltas are persisted with the node output, not merely checked and discarded.
    """
    migrator = AgentStateMigrator()

    def prepare(state: AgentState):
        migration = migrator.migrate(state)
        delta = {
            key: value
            for key, value in migration.state.items()
            if key not in state or state[key] != value
        }
        return migration.state, delta

    def sync_node(state: AgentState):
        migrated, delta = prepare(state)
        return {**delta, **node(migrated)}

    async def async_node(state: AgentState):
        migrated, delta = prepare(state)
        return {**delta, **(await node(migrated))}

    return async_node if iscoroutinefunction(node) else sync_node
