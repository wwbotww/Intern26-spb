from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from ..domain.agent_actions import RequiredInput
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.results import AgentResult, AgentResultStatus


MessageText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]
PublicResultType = Literal[
    "policy",
    "device_price",
    "tracking",
    "delivery_time",
    "postage",
]


class AgentNextAction(StrEnum):
    COLLECT_SLOTS = "collect_slots"
    CLARIFY_INTENT = "clarify_intent"
    COMPLETE = "complete"
    HANDOFF = "handoff"
    FAILED = "failed"


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


class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None
    message: MessageText | None = None
    explicit_intent: Intent | None = None
    confirm_overwrite: bool = False
    stream: bool = False

    @model_validator(mode="after")
    def validate_user_input(self) -> "AgentMessageRequest":
        if self.message is None and self.explicit_intent is None:
            raise ValueError("必须提供 message 或 explicit_intent")
        if self.explicit_intent is Intent.UNKNOWN:
            raise ValueError("explicit_intent 不能是 unknown")
        return self


class RequiredInputResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    type: Literal["string", "number", "region", "choice"]
    validation_hint: str = ""
    choices: list[str] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, value: RequiredInput) -> "RequiredInputResponse":
        return cls.model_validate(value.model_dump(mode="json"))


class AgentResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: PublicResultType
    status: AgentResultStatus
    data: dict[str, Any] | None = None
    reason_code: str = ""

    @classmethod
    def from_domain(cls, value: AgentResult) -> "AgentResultResponse":
        if value.intent is Intent.UNKNOWN:
            raise ValueError("AgentResult 不能使用 unknown 意图")
        return cls(
            type=value.intent.value,
            status=value.status,
            data=(
                value.data.model_dump(mode="json")
                if value.data is not None
                else None
            ),
            reason_code=value.reason_code,
        )


class AgentFailureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: FailureCategory
    code: str
    retryable: bool
    retry_after_seconds: float | None = None

    @classmethod
    def from_domain(cls, value: AgentFailure) -> "AgentFailureResponse":
        return cls(
            category=value.category,
            code=value.code,
            retryable=value.retryable,
            retry_after_seconds=value.retry_after_seconds,
        )


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    conversation_id: UUID
    turn_id: UUID
    phase: AgentPhase
    intent: Intent | None = None
    reply: str
    next_action: AgentNextAction
    required_inputs: list[RequiredInputResponse]
    result: AgentResultResponse | None = None
    failure: AgentFailureResponse | None = None
    warnings: list[str]

    @model_validator(mode="after")
    def validate_public_state(self) -> "AgentResponse":
        if self.phase is AgentPhase.FAILED and self.failure is None:
            raise ValueError("failed 响应必须包含 failure")
        if self.phase is not AgentPhase.FAILED and self.failure is not None:
            raise ValueError("只有 failed 响应可以包含 failure")
        if self.result is not None:
            if self.phase is not AgentPhase.COMPLETED:
                raise ValueError("只有 completed 响应可以包含 result")
            if self.intent is None or self.result.type != self.intent.value:
                raise ValueError("result.type 必须与响应 intent 一致")
        if (
            self.phase is AgentPhase.WAITING_USER
            and not self.required_inputs
        ):
            raise ValueError("waiting_user 响应必须包含 required_inputs")
        return self

    @classmethod
    def from_runtime(
        cls,
        *,
        request_id: str,
        output: Mapping[str, Any],
    ) -> "AgentResponse":
        phase = AgentPhase(str(output.get("phase", "")))
        required_inputs = [
            RequiredInput.model_validate(item)
            for item in output.get("required_inputs", [])
        ]
        raw_intent = output.get("active_intent")
        intent = Intent(str(raw_intent)) if raw_intent else None
        raw_result = output.get("result")
        result = (
            AgentResultResponse.from_domain(
                AgentResult.model_validate(raw_result)
            )
            if raw_result is not None
            else None
        )
        raw_failure = output.get("failure")
        failure = (
            AgentFailureResponse.from_domain(
                AgentFailure.model_validate(raw_failure)
            )
            if raw_failure is not None
            else None
        )
        return cls(
            request_id=request_id,
            conversation_id=UUID(str(output.get("conversation_id", ""))),
            turn_id=UUID(str(output.get("turn_id", ""))),
            phase=phase,
            intent=intent,
            reply=str(output.get("reply", "")),
            next_action=_next_action(phase, required_inputs),
            required_inputs=[
                RequiredInputResponse.from_domain(item)
                for item in required_inputs
            ],
            result=result,
            failure=failure,
            warnings=[str(item) for item in output.get("warnings", [])],
        )


class AgentCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: PublicResultType
    display_name: str
    available: bool
    capability_version: str | None = None
    required_inputs: list[RequiredInputResponse]


AgentReadinessState = Literal[
    "ready",
    "not_ready",
    "degraded",
    "starting",
    "disabled",
]


class AgentHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "not_ready"]
    service: Literal["spb-assistant-agent-v2"] = (
        "spb-assistant-agent-v2"
    )
    version: str
    phase: Literal[4] = 4
    checks: dict[str, AgentReadinessState]


class AgentError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str
    category: FailureCategory | None = None
    retryable: bool = False
    retry_after_seconds: float | None = Field(default=None, ge=0)


class AgentErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: AgentError


class AgentStreamStatusEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    stage: Literal["accepted"] = "accepted"
    message: str


class AgentStreamStateEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    conversation_id: UUID
    turn_id: UUID
    phase: AgentPhase
    intent: Intent | None = None
    next_action: AgentNextAction


class AgentStreamInputRequiredEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    conversation_id: UUID
    turn_id: UUID
    required_inputs: list[RequiredInputResponse]


class AgentStreamResultEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    conversation_id: UUID
    turn_id: UUID
    result: AgentResultResponse | None = None
    failure: AgentFailureResponse | None = None
    warnings: list[str] = Field(default_factory=list)


class AgentStreamDeltaEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    conversation_id: UUID
    turn_id: UUID
    content: str


class AgentStreamDoneEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    response: AgentResponse


class AgentStreamErrorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    code: str
    message: str
    http_status: int = Field(ge=400, le=599)
    category: FailureCategory | None = None
    retryable: bool = False
    retry_after_seconds: float | None = Field(default=None, ge=0)


def _next_action(
    phase: AgentPhase,
    required_inputs: list[RequiredInput],
) -> AgentNextAction:
    if phase is AgentPhase.WAITING_USER:
        if any(item.name == "intent" for item in required_inputs):
            return AgentNextAction.CLARIFY_INTENT
        return AgentNextAction.COLLECT_SLOTS
    if phase is AgentPhase.COMPLETED:
        return AgentNextAction.COMPLETE
    if phase is AgentPhase.HANDOFF:
        return AgentNextAction.HANDOFF
    if phase is AgentPhase.FAILED:
        return AgentNextAction.FAILED
    raise ValueError(f"不可投影未停止的 Agent phase: {phase.value}")
