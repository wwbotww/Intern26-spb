from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .primitives import MailNumber, MessageText
from .slots import RegionRef, WeightValue


class PolicyCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Literal["policy"] = "policy"
    question: MessageText


class DevicePriceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Literal["device_price"] = "device_price"
    question: MessageText


class TrackingCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Literal["tracking"] = "tracking"
    mail_no: MailNumber


class DeliveryTimeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Literal["delivery_time"] = "delivery_time"
    origin: RegionRef
    destination: RegionRef
    product_code: str | None = None


class PostageCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Literal["postage"] = "postage"
    origin: RegionRef
    destination: RegionRef
    weight: WeightValue
    product_code: str | None = None
    declared_value: Decimal | None = Field(default=None, ge=0)


AgentCommand = Annotated[
    PolicyCommand
    | DevicePriceCommand
    | TrackingCommand
    | DeliveryTimeCommand
    | PostageCommand,
    Field(discriminator="intent"),
]
