from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .primitives import MailNumber, MessageText


RegionCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
RegionName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class RegionResolution(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class RegionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: RegionName
    province_code: RegionCode | None = None
    city_code: RegionCode | None = None
    county_code: RegionCode | None = None


class RegionRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_text: RegionName
    province_code: RegionCode | None = None
    city_code: RegionCode | None = None
    county_code: RegionCode | None = None
    canonical_name: RegionName | None = None
    resolution: RegionResolution = RegionResolution.UNRESOLVED
    candidates: list[RegionCandidate] = Field(default_factory=list)


class WeightValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: Decimal | None = Field(default=None, gt=0)
    unit: Literal["g", "kg"] = "kg"


class TrackingSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["tracking"] = "tracking"
    mail_no: MailNumber | None = None


class DeliveryTimeSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["delivery_time"] = "delivery_time"
    origin: RegionRef | None = None
    destination: RegionRef | None = None


class PostageSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["postage"] = "postage"
    origin: RegionRef | None = None
    destination: RegionRef | None = None
    weight: WeightValue | None = None
    product_code: str | None = None


class PolicySlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["policy"] = "policy"
    question: MessageText


class DevicePriceSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["device_price"] = "device_price"
    question: MessageText


SlotPayload = Annotated[
    TrackingSlots
    | DeliveryTimeSlots
    | PostageSlots
    | PolicySlots
    | DevicePriceSlots,
    Field(discriminator="intent"),
]


class SlotProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: str
    source: Literal[
        "explicit_ui",
        "current_turn",
        "workflow_state",
        "rule_extractor",
        "model_extractor",
    ]
    raw_text: str = ""
