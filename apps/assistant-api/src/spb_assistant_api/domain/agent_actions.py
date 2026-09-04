from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .commands import AgentCommand
from .intents import Intent
from .primitives import MailNumber, MessageText
from .understanding import ControlDirective


class AgentMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: MessageText


class RequiredInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    type: Literal["string", "number", "region", "choice"] = "string"
    validation_hint: str = ""
    choices: list[str] = Field(default_factory=list)


class ClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["collect_slots"] = "collect_slots"
    intent: Intent
    prompt: str
    required_inputs: list[RequiredInput] = Field(default_factory=list)


class IntentClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["clarify_intent"] = "clarify_intent"
    prompt: str
    candidates: list[Intent] = Field(min_length=1)


class AgentResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: MessageText | None = None
    selected_intent: Intent | None = None
    confirm_overwrite: bool = False
    turn_id: UUID | None = None
    deadline_at: datetime | None = None

    @model_validator(mode="after")
    def require_user_value(self) -> "AgentResumeInput":
        if self.message is None and self.selected_intent is None:
            raise ValueError("恢复 Workflow 时必须提供消息或确认意图")
        if (
            self.deadline_at is not None
            and (
                self.deadline_at.tzinfo is None
                or self.deadline_at.utcoffset() is None
            )
        ):
            raise ValueError("deadline_at 必须包含时区")
        return self


class TrackingResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mail_no: MailNumber
    turn_id: UUID | None = None
    deadline_at: datetime | None = None

    @model_validator(mode="after")
    def validate_deadline(self) -> "TrackingResume":
        if (
            self.deadline_at is not None
            and (
                self.deadline_at.tzinfo is None
                or self.deadline_at.utcoffset() is None
            )
        ):
            raise ValueError("deadline_at 必须包含时区")
        return self


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UnderstandAction(ActionBase):
    type: Literal["understand"] = "understand"


class ClarifyIntentAction(ActionBase):
    type: Literal["clarify_intent"] = "clarify_intent"
    candidates: list[Intent] = Field(min_length=1)
    prompt: str

    @model_validator(mode="after")
    def validate_candidates(self) -> "ClarifyIntentAction":
        if Intent.UNKNOWN in self.candidates:
            raise ValueError("unknown 不能作为意图确认选项")
        if len(self.candidates) != len(set(self.candidates)):
            raise ValueError("意图确认选项不能重复")
        return self


class CollectSlotsAction(ActionBase):
    type: Literal["collect_slots"] = "collect_slots"
    intent: Intent
    required_inputs: list[RequiredInput] = Field(default_factory=list)
    prompt: str


class InvokeToolAction(ActionBase):
    type: Literal["invoke_tool"] = "invoke_tool"
    tool_name: str
    command: AgentCommand
    tool_call_id: UUID
    argument_fingerprint: str
    attempt: int = Field(ge=1)
    deadline_at: datetime

    @model_validator(mode="after")
    def validate_deadline(self) -> "InvokeToolAction":
        if (
            self.deadline_at.tzinfo is None
            or self.deadline_at.utcoffset() is None
        ):
            raise ValueError("deadline_at 必须包含时区")
        return self


class ValidateResultAction(ActionBase):
    type: Literal["validate_result"] = "validate_result"


class RespondAction(ActionBase):
    type: Literal["respond"] = "respond"


class HandoffAction(ActionBase):
    type: Literal["handoff"] = "handoff"
    reason_code: str


class ControlAction(ActionBase):
    type: Literal["control"] = "control"
    directive: ControlDirective

    @model_validator(mode="after")
    def validate_directive(self) -> "ControlAction":
        if self.directive is ControlDirective.NONE:
            raise ValueError("ControlAction 必须包含有效控制命令")
        return self


NextAction = Annotated[
    UnderstandAction
    | ClarifyIntentAction
    | CollectSlotsAction
    | InvokeToolAction
    | ValidateResultAction
    | RespondAction
    | HandoffAction
    | ControlAction,
    Field(discriminator="type"),
]
