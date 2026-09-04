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


class PolicyEvidenceData(BaseModel):
    """Public, typed projection of one grounded V1 policy citation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["policy"] = "policy"
    evidence_id: str
    title: str
    source_url: str
    excerpt: str
    document_no: str = ""
    published_at: str = ""
    source_org: str = ""
    section_path: str = ""
    chunk_id: str = ""
    document_id: str = ""
    score: float = 0.0
    rerank_score: float | None = None


class PolicyData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["policy"] = "policy"
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[PolicyEvidenceData] = Field(default_factory=list)

    @model_validator(mode="after")
    def synchronize_evidence_ids(self) -> "PolicyData":
        projected = [item.evidence_id for item in self.evidence]
        if len(projected) != len(set(projected)):
            raise ValueError("政策证据 ID 不能重复")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("政策 evidence_ids 不能重复")
        if projected:
            if self.evidence_ids and self.evidence_ids != projected:
                raise ValueError("政策 evidence_ids 必须与 evidence 顺序一致")
            self.evidence_ids = projected
        return self


class DevicePriceEvidenceData(BaseModel):
    """Public, typed projection of one grounded V1 price record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["device_price"] = "device_price"
    evidence_id: str
    title: str
    brand: str
    model: str
    specification: str
    price: str
    currency: str
    source: str
    observed_at: str
    availability: str = ""
    source_url: str = ""
    original_price: str | None = None
    original_price_type: str = ""
    official_product_id: str = ""
    official_sku_id: str = ""
    match_score: float = 0.0


class DevicePriceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["device_price"] = "device_price"
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[DevicePriceEvidenceData] = Field(default_factory=list)

    @model_validator(mode="after")
    def synchronize_evidence_ids(self) -> "DevicePriceData":
        projected = [item.evidence_id for item in self.evidence]
        if len(projected) != len(set(projected)):
            raise ValueError("设备价格证据 ID 不能重复")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("设备价格 evidence_ids 不能重复")
        if projected:
            if self.evidence_ids and self.evidence_ids != projected:
                raise ValueError(
                    "设备价格 evidence_ids 必须与 evidence 顺序一致"
                )
            self.evidence_ids = projected
        return self


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
    estimated_duration: Decimal = Field(gt=0)
    duration_unit: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=32),
    ]
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
