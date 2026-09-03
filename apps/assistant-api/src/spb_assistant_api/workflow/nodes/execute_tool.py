from __future__ import annotations

from ...domain.agent_actions import InvokeToolAction
from ...domain.agent_errors import AgentOperationError
from ...domain.agent_events import (
    AgentEventType,
    ToolCallRecord,
    ToolCallStatus,
)
from ...services.agent_tools import ToolExecutor
from ..node_utils import agent_event
from ..state import AgentState


def create_execute_tool_node(executor: ToolExecutor):
    async def execute_tool(state: AgentState) -> dict[str, object]:
        action = InvokeToolAction.model_validate(state.get("pending_action"))
        conversation_id = state.get("conversation_id", "")
        prior_fingerprints = {
            ToolCallRecord.model_validate(item).argument_fingerprint
            for item in state.get("tool_calls", [])
        }
        logical_call_count = int(state.get("tool_call_count", 0))
        if action.argument_fingerprint not in prior_fingerprints:
            logical_call_count += 1

        started = ToolCallRecord(
            tool_call_id=action.tool_call_id,
            tool_name=action.tool_name,
            argument_fingerprint=action.argument_fingerprint,
            attempt=action.attempt,
            status=ToolCallStatus.STARTED,
        )
        try:
            outcome = await executor.execute(
                conversation_id=conversation_id,
                action=action,
            )
        except AgentOperationError as error:
            failed = ToolCallRecord(
                tool_call_id=action.tool_call_id,
                tool_name=action.tool_name,
                argument_fingerprint=action.argument_fingerprint,
                attempt=action.attempt,
                status=ToolCallStatus.FAILED,
                failure_category=error.failure.category,
            )
            return {
                "phase": "validating",
                "last_result": None,
                "last_error": error.failure.model_dump(mode="json"),
                "tool_call_count": logical_call_count,
                "tool_calls": [
                    started.model_dump(mode="json"),
                    failed.model_dump(mode="json"),
                ],
                "audit_events": [
                    agent_event(
                        AgentEventType.TOOL_CALL_STARTED,
                        node="execute_tool",
                        phase="executing",
                        tool=action.tool_name,
                        attempt=action.attempt,
                    ),
                    agent_event(
                        AgentEventType.TOOL_CALL_FAILED,
                        node="execute_tool",
                        phase="validating",
                        tool=action.tool_name,
                        attempt=action.attempt,
                        failure_category=error.failure.category.value,
                    ),
                ],
            }

        status = (
            ToolCallStatus.REUSED
            if outcome.reused
            else ToolCallStatus.SUCCEEDED
        )
        completed = ToolCallRecord(
            tool_call_id=action.tool_call_id,
            tool_name=action.tool_name,
            argument_fingerprint=action.argument_fingerprint,
            attempt=action.attempt,
            status=status,
            result_status=outcome.result.status,
        )
        event_type = (
            AgentEventType.TOOL_CALL_REUSED
            if outcome.reused
            else AgentEventType.TOOL_CALL_SUCCEEDED
        )
        return {
            "phase": "validating",
            "last_result": outcome.result.model_dump(mode="json"),
            "last_error": None,
            "tool_call_count": logical_call_count,
            "tool_calls": [
                started.model_dump(mode="json"),
                completed.model_dump(mode="json"),
            ],
            "audit_events": [
                agent_event(
                    AgentEventType.TOOL_CALL_STARTED,
                    node="execute_tool",
                    phase="executing",
                    tool=action.tool_name,
                    attempt=action.attempt,
                ),
                agent_event(
                    event_type,
                    node="execute_tool",
                    phase="validating",
                    tool=action.tool_name,
                    attempt=action.attempt,
                ),
            ],
        }

    return execute_tool
