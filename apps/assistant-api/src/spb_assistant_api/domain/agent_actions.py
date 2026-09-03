from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .commands import AgentCommand
from .intents import Intent
from .primitives import MailNumber, MessageText


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


class TrackingResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mail_no: MailNumber
    turn_id: UUID | None = None
    deadline_at: datetime | None = None


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UnderstandAction(ActionBase):
    type: Literal["understand"] = "understand"


class ClarifyIntentAction(ActionBase):
    type: Literal["clarify_intent"] = "clarify_intent"
    candidates: list[Intent] = Field(default_factory=list)
    prompt: str


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


class ValidateResultAction(ActionBase):
    type: Literal["validate_result"] = "validate_result"


class RespondAction(ActionBase):
    type: Literal["respond"] = "respond"


class HandoffAction(ActionBase):
    type: Literal["handoff"] = "handoff"
    reason_code: str


NextAction = Annotated[
    UnderstandAction
    | ClarifyIntentAction
    | CollectSlotsAction
    | InvokeToolAction
    | ValidateResultAction
    | RespondAction
    | HandoffAction,
    Field(discriminator="type"),
]
