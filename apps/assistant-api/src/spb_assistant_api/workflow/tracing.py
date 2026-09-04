from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from pydantic import ValidationError

from ..domain.agent_events import AgentEvent


TraceScalar: TypeAlias = str | int | bool | None
TraceEventWindow: TypeAlias = Literal["delta", "tail_fallback", "empty"]
WorkflowTraceSink: TypeAlias = Callable[["AgentWorkflowTrace"], None]

_SAFE_DETAIL_KEYS = frozenset(
    {
        "action",
        "ambiguity_count",
        "attempt",
        "clarification_type",
        "control",
        "delay_ms",
        "directive",
        "failure_category",
        "finish_reason",
        "intent",
        "missing_slot_count",
        "multi_intent",
        "parser_version",
        "prompt_version",
        "result_status",
        "retry",
        "retry_delay_exceeded",
        "slot",
        "source",
        "step",
        "tool",
    }
)
_SAFE_CODE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$")
_MAX_TRACE_EVENTS = 64


@dataclass(frozen=True, slots=True)
class AgentWorkflowStepTrace:
    """One sanitized semantic event emitted by an Agent node."""

    sequence: int
    event: str
    node: str
    phase: str
    details: tuple[tuple[str, TraceScalar], ...] = ()

    def as_log_value(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event": self.event,
            "node": self.node,
            "phase": self.phase,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class AgentWorkflowTrace:
    """Bounded, privacy-safe projection of one LangGraph invocation."""

    conversation_id: str | None
    turn_id: str | None
    outcome: str
    resumed: bool
    interrupted: bool
    checkpoint_before: bool
    checkpoint_after: bool
    event_window: TraceEventWindow
    steps: tuple[AgentWorkflowStepTrace, ...]
    node_path: tuple[str, ...]
    edge_path: tuple[str, ...]
    truncated_event_count: int = 0
    invalid_event_count: int = 0
    loop_step_count: int = 0
    logical_tool_call_count: int = 0
    retry_count: int = 0
    failure_category: str | None = None
    failure_code: str | None = None


def build_agent_workflow_trace(
    *,
    before_state: Mapping[str, Any] | None,
    after_state: Mapping[str, Any] | None,
    after_next: Sequence[str] = (),
    resumed: bool,
    checkpoint_before: bool,
    checkpoint_after: bool,
    fallback_output: Mapping[str, Any] | None = None,
    outcome_override: str | None = None,
    failure_category: str | None = None,
    failure_code: str | None = None,
) -> AgentWorkflowTrace:
    """Project checkpoint state without forwarding arbitrary graph values.

    The reducer retains audit events for a whole conversation. The trace uses
    the before/after event-count delta so each log entry contains only events
    produced by the current invocation. If the pre-run checkpoint cannot be
    observed, only a bounded tail is emitted and that fallback is explicit.
    """

    before = before_state or {}
    after = after_state or fallback_output or {}
    raw_events = _event_list(after.get("audit_events"))
    before_events = _event_list(before.get("audit_events"))
    if not raw_events:
        selected_events: list[Any] = []
        event_window: TraceEventWindow = "empty"
    elif before_state is not None and len(before_events) <= len(raw_events):
        selected_events = raw_events[len(before_events) :]
        event_window = "delta"
    else:
        selected_events = raw_events
        event_window = "tail_fallback"

    truncated_event_count = max(0, len(selected_events) - _MAX_TRACE_EVENTS)
    selected_events = selected_events[-_MAX_TRACE_EVENTS:]
    steps: list[AgentWorkflowStepTrace] = []
    invalid_event_count = 0
    for raw_event in selected_events:
        try:
            event = AgentEvent.model_validate(raw_event)
        except (TypeError, ValidationError):
            invalid_event_count += 1
            continue
        details = tuple(
            (key, _sanitize_detail(value))
            for key, value in sorted(event.details.items())
            if key in _SAFE_DETAIL_KEYS
        )
        steps.append(
            AgentWorkflowStepTrace(
                sequence=len(steps) + 1,
                event=event.event_type.value,
                node=_safe_code(event.node),
                phase=_safe_code(event.phase),
                details=details,
            )
        )

    output = fallback_output or {}
    outcome = _safe_code(
        str(
            outcome_override
            or after.get("phase")
            or output.get("phase")
            or "error"
        )
    )
    interrupted = outcome == "waiting_user" or "clarify" in after_next
    node_path = _node_path(steps, interrupted=interrupted)
    edge_path = _edge_path(
        node_path,
        interrupted=interrupted,
        failed_without_terminal=outcome == "error",
    )
    state_failure = after.get("failure")
    if isinstance(state_failure, Mapping):
        failure_category = failure_category or _optional_code(
            state_failure.get("category")
        )
        failure_code = failure_code or _optional_code(
            state_failure.get("code")
        )

    return AgentWorkflowTrace(
        conversation_id=_optional_text(
            after.get("conversation_id")
            or output.get("conversation_id")
            or before.get("conversation_id")
        ),
        turn_id=_optional_text(
            after.get("turn_id")
            or output.get("turn_id")
            or before.get("turn_id")
        ),
        outcome=outcome,
        resumed=resumed,
        interrupted=interrupted,
        checkpoint_before=checkpoint_before,
        checkpoint_after=checkpoint_after,
        event_window=event_window,
        steps=tuple(steps),
        node_path=node_path,
        edge_path=edge_path,
        truncated_event_count=truncated_event_count,
        invalid_event_count=invalid_event_count,
        loop_step_count=_non_negative_int(after.get("step_count")),
        logical_tool_call_count=_non_negative_int(
            after.get("tool_call_count")
        ),
        retry_count=_non_negative_int(after.get("retry_count")),
        failure_category=_optional_code(failure_category),
        failure_code=_optional_code(failure_code),
    )


def _event_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _sanitize_detail(value: TraceScalar) -> TraceScalar:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(-1_000_000, min(value, 1_000_000))
    if isinstance(value, str):
        return _safe_code(value)
    return "unclassified"


def _safe_code(value: str) -> str:
    return value if _SAFE_CODE_RE.fullmatch(value) else "unclassified"


def _optional_code(value: Any) -> str | None:
    if value is None:
        return None
    return _safe_code(str(value))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized[:255] or None


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, min(int(value), 1_000_000))
    except (TypeError, ValueError):
        return 0


def _node_path(
    steps: Sequence[AgentWorkflowStepTrace],
    *,
    interrupted: bool,
) -> tuple[str, ...]:
    nodes: list[str] = []
    for step in steps:
        if not nodes or nodes[-1] != step.node:
            nodes.append(step.node)
    # The interrupt is raised inside clarify before that node can return its
    # own state update, so the semantic event stream must infer this one node.
    if interrupted and (not nodes or nodes[-1] != "clarify"):
        nodes.append("clarify")
    return tuple(nodes)


def _edge_path(
    nodes: Sequence[str],
    *,
    interrupted: bool,
    failed_without_terminal: bool,
) -> tuple[str, ...]:
    if not nodes:
        return ()
    edges = [f"__start__->{nodes[0]}"]
    edges.extend(
        f"{source}->{target}"
        for source, target in zip(nodes, nodes[1:], strict=False)
    )
    terminal = (
        "__interrupt__"
        if interrupted
        else "__error__"
        if failed_without_terminal
        else "__end__"
    )
    edges.append(f"{nodes[-1]}->{terminal}")
    return tuple(edges)
