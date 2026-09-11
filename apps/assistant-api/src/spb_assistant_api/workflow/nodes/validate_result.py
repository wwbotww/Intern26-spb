from __future__ import annotations

from datetime import UTC, datetime

from ...domain.agent_actions import InvokeToolAction
from ...domain.agent_errors import AgentOperationError
from ...domain.agent_events import AgentEventType
from ...domain.failures import AgentFailure
from ...domain.results import AgentResult, AgentResultStatus
from ...domain.product_price_execution import ProductPriceData
from ...domain.product_price_slots import ProductPriceCommand
from ...services.result_validator import AgentResultValidator
from ..node_utils import agent_event
from ..state import AgentState
from ..price_candidates import issue_candidates


def create_validate_result_node(validator: AgentResultValidator, *, clock=None):
    resolved_clock = clock or (lambda: datetime.now(UTC))
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
        price_update = {}
        phase = "responding"
        if isinstance(action.command, ProductPriceCommand) and validated.status is AgentResultStatus.NEED_MORE_INFO:
            try:
                candidates = (
                    issue_candidates(state, action.command, validated.data, resolved_clock())
                    if isinstance(validated.data, ProductPriceData) else None
                )
            except AgentOperationError as error:
                return {"phase": "recovering", "last_result": None,
                        "last_error": error.failure.model_dump(mode="json")}
            phase = "ready"
            price_update = {
                "price_candidates": candidates.model_dump(mode="json") if candidates else None,
                "price_selected_token": None, "price_result_slots": validated.missing_slots,
                "last_result": None, "warnings": validated.warnings,
            }
        return {
            "phase": phase,
            "last_result": validated.model_dump(mode="json"),
            **price_update,
            "audit_events": [
                agent_event(
                    AgentEventType.RESULT_VALIDATED,
                    node="validate_result",
                    phase=phase,
                    result_status=validated.status.value,
                )
            ],
        }

    return validate_result
