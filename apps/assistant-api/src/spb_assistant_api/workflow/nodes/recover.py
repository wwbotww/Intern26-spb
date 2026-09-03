from __future__ import annotations

from ...domain.agent_events import AgentEventType
from ...domain.failures import AgentFailure
from ..node_utils import agent_event
from ..policy import WorkflowPolicy
from ..state import AgentState


def create_recover_node(policy: WorkflowPolicy):
    def recover(state: AgentState) -> dict[str, object]:
        failure = AgentFailure.model_validate(state.get("last_error"))
        retry_count = int(state.get("retry_count", 0))
        decision = policy.recover(
            failure,
            retry_count=retry_count,
            max_retries=int(state.get("max_retries", 1)),
        )
        if decision.retry:
            return {
                "phase": "ready",
                "pending_action": None,
                "last_error": None,
                "failure": None,
                "retry_count": retry_count + 1,
                "audit_events": [
                    agent_event(
                        AgentEventType.RECOVERY_SCHEDULED,
                        node="recover",
                        phase="ready",
                        retry=retry_count + 1,
                        failure_category=failure.category.value,
                    )
                ],
            }
        return {
            "phase": "responding",
            "audit_events": [
                agent_event(
                    AgentEventType.RECOVERY_SCHEDULED,
                    node="recover",
                    phase="responding",
                    retry=False,
                    failure_category=failure.category.value,
                )
            ],
        }

    return recover
