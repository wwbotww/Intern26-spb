from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from ..workflow.tracing import AgentWorkflowTrace


logger = logging.getLogger("spb_assistant_api.agent_trace")
workflow_logger = logging.getLogger("spb_assistant_api.workflow_trace")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class AgentRunTrace:
    """Bounded operational trace; deliberately excludes prompts and state."""

    transport: str
    outcome: str
    duration_seconds: float
    conversation_id: UUID | None = None
    turn_id: UUID | None = None
    phase: str | None = None
    intent: str | None = None
    next_action: str | None = None
    required_input_names: tuple[str, ...] = ()
    result_type: str | None = None
    result_status: str | None = None
    reason_code: str | None = None
    failure_category: str | None = None
    failure_code: str | None = None
    retryable: bool | None = None
    warning_count: int = 0

    def log(self) -> None:
        logger.info("agent_run_trace", extra=self.as_log_fields())

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "trace_schema_version": "1",
            "trace_type": "agent_run",
            "transport": self.transport,
            "outcome": self.outcome,
            "duration_ms": round(max(0.0, self.duration_seconds) * 1000, 3),
            "conversation_ref": _opaque_ref(self.conversation_id),
            "turn_ref": _opaque_ref(self.turn_id),
            "phase": self.phase,
            "intent": self.intent,
            "next_action": self.next_action,
            "required_input_names": list(self.required_input_names),
            "result_type": self.result_type,
            "result_status": self.result_status,
            "reason_code": _bounded_code(self.reason_code),
            "failure_category": self.failure_category,
            "failure_code": _bounded_code(self.failure_code),
            "retryable": self.retryable,
            "warning_count": max(0, self.warning_count),
        }


def log_agent_workflow_trace(trace: "AgentWorkflowTrace") -> None:
    """Emit the sanitized runtime projection on a dedicated logger."""

    workflow_logger.info(
        "agent_workflow_trace",
        extra={
            "trace_schema_version": "1",
            "trace_type": "agent_workflow",
            "conversation_ref": _opaque_ref(trace.conversation_id),
            "turn_ref": _opaque_ref(trace.turn_id),
            "outcome": trace.outcome,
            "resumed": trace.resumed,
            "interrupted": trace.interrupted,
            "checkpoint_before": trace.checkpoint_before,
            "checkpoint_after": trace.checkpoint_after,
            "event_window": trace.event_window,
            "event_count": len(trace.steps),
            "truncated_event_count": trace.truncated_event_count,
            "invalid_event_count": trace.invalid_event_count,
            "loop_step_count": trace.loop_step_count,
            "logical_tool_call_count": trace.logical_tool_call_count,
            "retry_count": trace.retry_count,
            "failure_category": _bounded_code(trace.failure_category),
            "failure_code": _bounded_code(trace.failure_code),
            "node_path": list(trace.node_path),
            "edge_path": list(trace.edge_path),
            "steps": [step.as_log_value() for step in trace.steps],
        },
    )


def _opaque_ref(value: UUID | str | None) -> str | None:
    if value is None:
        return None
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def _bounded_code(value: str | None) -> str | None:
    if value is None:
        return None
    return value if _CODE_RE.fullmatch(value) else "unclassified"
