from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict

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
