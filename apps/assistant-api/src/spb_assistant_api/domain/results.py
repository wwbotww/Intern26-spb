from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .intents import Intent
from .primitives import MailNumber
from .slots import RegionRef, WeightValue


CurrencyCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[A-Z]{3}$",
    ),
]


class AgentResultStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    NEED_MORE_INFO = "need_more_info"
    NO_MATCH = "no_match"
    FAILED = "failed"


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: str
    source_name: str
    record_id: str = ""
    source_url: str = ""
    queried_at: datetime | None = None


class PolicyData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["policy"] = "policy"
    evidence_ids: list[str] = Field(default_factory=list)


class DevicePriceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["device_price"] = "device_price"
    evidence_ids: list[str] = Field(default_factory=list)


class TrackingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_code: str = ""
    description: str
    occurred_at: datetime
    location: str = ""


class TrackingData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tracking"] = "tracking"
    mail_no: MailNumber
    current_status: str
    events: list[TrackingEvent] = Field(default_factory=list)
    queried_at: datetime


class DeliveryTimeData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["delivery_time"] = "delivery_time"
    origin: RegionRef
    destination: RegionRef
    estimated_duration: Decimal
    duration_unit: str
    service_level: str = ""
    estimate_basis: str = ""
    queried_at: datetime


class PostageData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["postage"] = "postage"
    origin: RegionRef
    destination: RegionRef
    input_weight: WeightValue
    billable_weight: WeightValue | None = None
    amount: Decimal = Field(ge=0)
    currency: CurrencyCode
    product_code: str = ""
    queried_at: datetime


AgentData = Annotated[
    PolicyData
    | DevicePriceData
    | TrackingData
    | DeliveryTimeData
    | PostageData,
    Field(discriminator="type"),
]


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    intent: Intent
    status: AgentResultStatus
    answer: str = ""
    data: AgentData | None = None
    warnings: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    reason_code: str = ""
    provenance: list[SourceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result_semantics(self) -> "AgentResult":
        if not self.tool.strip():
            raise ValueError("tool 不能为空")
        if self.data is not None and self.data.type != self.intent.value:
            raise ValueError("result data 类型必须与 intent 一致")
        if self.status is AgentResultStatus.SUCCESS and not self.answer.strip():
            raise ValueError("成功结果必须包含 answer")
        if self.status is AgentResultStatus.NO_MATCH and self.data is not None:
            raise ValueError("无匹配结果不能包含事实 data")
        return self
