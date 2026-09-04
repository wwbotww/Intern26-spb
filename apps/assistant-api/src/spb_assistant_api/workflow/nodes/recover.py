from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ...domain.agent_events import AgentEventType
from ...domain.failures import AgentFailure
from ...services.retry_schedule import RetrySchedule
from ..node_utils import agent_event
from ..policy import WorkflowPolicy
from ..state import AgentState


def create_recover_node(
    policy: WorkflowPolicy,
    *,
    retry_schedule: RetrySchedule | None = None,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
):
    schedule = retry_schedule or RetrySchedule()

    async def recover(state: AgentState) -> dict[str, object]:
        failure = AgentFailure.model_validate(state.get("last_error"))
        retry_count = int(state.get("retry_count", 0))
        decision = policy.recover(
            failure,
            retry_count=retry_count,
            max_retries=int(state.get("max_retries", 1)),
        )
        retry_number = retry_count + 1
        delay_seconds = (
            schedule.delay_seconds(
                failure=failure,
                retry_number=retry_number,
                jitter_key=(
                    f"{state.get('conversation_id', '')}:"
                    f"{state.get('turn_id', '')}"
                ),
            )
            if decision.retry
            else None
        )
        if decision.retry and delay_seconds is not None:
            if delay_seconds > 0:
                await sleeper(delay_seconds)
            return {
                "phase": "ready",
                "pending_action": None,
                "last_error": None,
                "failure": None,
                "retry_count": retry_number,
                "audit_events": [
                    agent_event(
                        AgentEventType.RECOVERY_SCHEDULED,
                        node="recover",
                        phase="ready",
                        retry=retry_number,
                        delay_ms=round(delay_seconds * 1000),
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
                    retry_delay_exceeded=(
                        decision.retry and delay_seconds is None
                    ),
                )
            ],
        }

    return recover
