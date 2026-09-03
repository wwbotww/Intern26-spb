from __future__ import annotations

from enum import StrEnum
from typing import TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .failures import FailureCategory
from .results import AgentResultStatus


EventDetail: TypeAlias = str | int | bool | None


class AgentEventType(StrEnum):
    CONVERSATION_STARTED = "conversation_started"
    USER_MESSAGE_RECEIVED = "user_message_received"
    QUERY_UNDERSTOOD = "query_understood"
    CLARIFICATION_REQUESTED = "clarification_requested"
    CLARIFICATION_RESUMED = "clarification_resumed"
    ACTION_DECIDED = "action_decided"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_REUSED = "tool_call_reused"
    TOOL_CALL_SUCCEEDED = "tool_call_succeeded"
    TOOL_CALL_FAILED = "tool_call_failed"
    RESULT_VALIDATED = "result_validated"
    FAILURE_CLASSIFIED = "failure_classified"
    RECOVERY_SCHEDULED = "recovery_scheduled"
    RESPONSE_PREPARED = "response_prepared"


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: AgentEventType
    node: str
    phase: str
    details: dict[str, EventDetail] = Field(default_factory=dict)


class ToolCallStatus(StrEnum):
    STARTED = "started"
    REUSED = "reused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: UUID
    tool_name: str
    argument_fingerprint: str
    attempt: int = Field(ge=1)
    status: ToolCallStatus
    result_status: AgentResultStatus | None = None
    failure_category: FailureCategory | None = None
