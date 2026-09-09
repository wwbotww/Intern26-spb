from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from .commands import (
    DeliveryTimeCommand,
    DevicePriceCommand,
    PolicyCommand,
    PostageCommand,
    TrackingCommand,
)
from .intents import Intent
from .results import AgentResult


CommandModel: TypeAlias = (
    PolicyCommand
    | DevicePriceCommand
    | TrackingCommand
    | DeliveryTimeCommand
    | PostageCommand
)


def argument_fingerprint(command: CommandModel) -> str:
    """Argument integrity only; never a conversation-wide cache identity."""
    payload = json.dumps(
        command.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class LegacyToolCallReference(BaseModel):
    """Identity copied only from a legacy checkpoint's pending invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: UUID
    tool_name: str
    argument_fingerprint: str


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    intent: Intent
    tool_name: str
    command_type: type[BaseModel]
    result_schema_name: str
    required_slots: tuple[str, ...]
    read_only: bool = True
    max_attempts: int = 2
    capability_version: str = "1"

    def __post_init__(self) -> None:
        if self.intent is Intent.UNKNOWN:
            raise ValueError("unknown 意图不能注册工具")
        if not self.tool_name.strip():
            raise ValueError("tool_name 不能为空")
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须大于 0")
        if not isinstance(self.command_type, type) or not issubclass(
            self.command_type,
            BaseModel,
        ):
            raise ValueError("command_type 必须是 Pydantic model")
        intent_field = self.command_type.model_fields.get("intent")
        if intent_field is None or intent_field.default != self.intent.value:
            raise ValueError("command_type 的 intent 必须与 Descriptor 一致")
        if not self.result_schema_name.strip():
            raise ValueError("result_schema_name 不能为空")
        if len(self.required_slots) != len(set(self.required_slots)):
            raise ValueError("required_slots 不能重复")
        if not self.capability_version.strip():
            raise ValueError("capability_version 不能为空")


class ToolExecutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    tool_call_id: UUID
    tool_name: str
    argument_fingerprint: str
    result: AgentResult
    completed_at: datetime

    @model_validator(mode="after")
    def validate_completed_at(self) -> "ToolExecutionReceipt":
        if (
            self.completed_at.tzinfo is None
            or self.completed_at.utcoffset() is None
        ):
            raise ValueError("completed_at 必须包含时区")
        return self
