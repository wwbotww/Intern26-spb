from __future__ import annotations

from enum import StrEnum
from operator import add
from typing import Annotated, Any, Literal

from typing_extensions import TypedDict

from .reducers import append_events, append_tool_calls


WorkflowPhase = Literal[
    "understanding",
    "clarifying",
    "ready",
    "completed",
]
FinishReason = Literal["stop"]


class SpikeState(TypedDict, total=False):
    message: str
    mail_no: str
    phase: WorkflowPhase
    reply: str
    finish_reason: FinishReason
    audit_events: Annotated[list[str], add]


class SpikeInputState(TypedDict):
    message: str


class SpikeOutputState(TypedDict, total=False):
    phase: WorkflowPhase
    reply: str
    finish_reason: FinishReason


class AgentPhase(StrEnum):
    NEW = "new"
    UNDERSTANDING = "understanding"
    CLARIFYING = "clarifying"
    COLLECTING = "collecting"
    READY = "ready"
    EXECUTING = "executing"
    VALIDATING = "validating"
    RECOVERING = "recovering"
    RESPONDING = "responding"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    HANDOFF = "handoff"
    FAILED = "failed"


AgentPhaseValue = Literal[
    "new",
    "understanding",
    "clarifying",
    "collecting",
    "ready",
    "executing",
    "validating",
    "recovering",
    "responding",
    "waiting_user",
    "completed",
    "handoff",
    "failed",
]
AgentFinishReason = Literal["stop", "handoff", "failed"]


class AgentState(TypedDict, total=False):
    schema_version: str
    conversation_id: str
    turn_id: str
    message: str
    latest_message: str
    explicit_intent: str | None
    phase: AgentPhaseValue
    turn_count: int
    active_intent: str | None
    candidate_intents: list[str]
    multi_intent: bool
    control: str
    slots: dict[str, Any] | None
    slot_provenance: list[dict[str, Any]]
    confirm_slot_overwrite: bool
    intent_choice_confirmed: bool
    missing_slots: list[str]
    ambiguities: list[str]
    pending_query: str
    understanding_parser_version: str
    understanding_prompt_version: str | None
    pending_action: dict[str, Any] | None
    tool_calls: Annotated[list[dict[str, Any]], append_tool_calls]
    audit_events: Annotated[list[dict[str, Any]], append_events]
    last_result: dict[str, Any] | None
    last_error: dict[str, Any] | None
    result: dict[str, Any] | None
    failure: dict[str, Any] | None
    tool_call_count: int
    retry_count: int
    step_count: int
    max_steps: int
    max_tool_calls: int
    max_retries: int
    deadline_at: str
    required_inputs: list[dict[str, Any]]
    reply: str
    warnings: list[str]
    finish_reason: AgentFinishReason | None


class AgentInputState(TypedDict, total=False):
    conversation_id: str
    turn_id: str
    message: str
    explicit_intent: str | None
    deadline_at: str
    max_steps: int
    max_tool_calls: int
    max_retries: int


class AgentOutputState(TypedDict, total=False):
    conversation_id: str
    turn_id: str
    phase: AgentPhaseValue
    active_intent: str | None
    reply: str
    required_inputs: list[dict[str, Any]]
    result: dict[str, Any] | None
    failure: dict[str, Any] | None
    warnings: list[str]
    finish_reason: AgentFinishReason | None
