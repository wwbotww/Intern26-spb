from __future__ import annotations

from ...domain.agent_actions import InvokeToolAction
from ...domain.agent_errors import AgentOperationError
from ...domain.agent_events import AgentEventType
from ...domain.failures import AgentFailure
from ...domain.results import AgentResult
from ...services.result_validator import AgentResultValidator
from ..node_utils import agent_event
from ..state import AgentState


def create_validate_result_node(validator: AgentResultValidator):
    def validate_result(state: AgentState) -> dict[str, object]:
        raw_error = state.get("last_error")
        if raw_error is not None:
            failure = AgentFailure.model_validate(raw_error)
            return {
                "phase": "recovering",
                "audit_events": [
                    agent_event(
                        AgentEventType.FAILURE_CLASSIFIED,
                        node="validate_result",
                        phase="recovering",
                        failure_category=failure.category.value,
                    )
                ],
            }

        action = InvokeToolAction.model_validate(state.get("pending_action"))
        result = AgentResult.model_validate(state.get("last_result"))
        try:
            validated = validator.validate(
                command=action.command,
                result=result,
            )
        except AgentOperationError as error:
            return {
                "phase": "recovering",
                "last_result": None,
                "last_error": error.failure.model_dump(mode="json"),
                "audit_events": [
                    agent_event(
                        AgentEventType.FAILURE_CLASSIFIED,
                        node="validate_result",
                        phase="recovering",
                        failure_category=error.failure.category.value,
                    )
                ],
            }
        return {
            "phase": "responding",
            "last_result": validated.model_dump(mode="json"),
            "audit_events": [
                agent_event(
                    AgentEventType.RESULT_VALIDATED,
                    node="validate_result",
                    phase="responding",
                    result_status=validated.status.value,
                )
            ],
        }

    return validate_result
