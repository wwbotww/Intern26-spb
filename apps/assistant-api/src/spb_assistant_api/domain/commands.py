from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .primitives import MailNumber, MessageText
from .slots import RegionRef, RegionResolution, WeightValue


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

    @model_validator(mode="after")
    def validate_executable_route(self) -> "DeliveryTimeCommand":
        _require_resolved_route(self.origin, self.destination)
        return self


class PostageCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Literal["postage"] = "postage"
    origin: RegionRef
    destination: RegionRef
    weight: WeightValue
    product_code: str | None = None
    declared_value: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_executable_input(self) -> "PostageCommand":
        _require_resolved_route(self.origin, self.destination)
        if self.weight.value is None:
            raise ValueError("postage command requires a concrete weight")
        return self


def _require_resolved_route(
    origin: RegionRef,
    destination: RegionRef,
) -> None:
    if (
        origin.resolution is not RegionResolution.RESOLVED
        or destination.resolution is not RegionResolution.RESOLVED
    ):
        raise ValueError("command route requires resolved regions")


AgentCommand = Annotated[
    PolicyCommand
    | DevicePriceCommand
    | TrackingCommand
    | DeliveryTimeCommand
    | PostageCommand,
    Field(discriminator="intent"),
]
