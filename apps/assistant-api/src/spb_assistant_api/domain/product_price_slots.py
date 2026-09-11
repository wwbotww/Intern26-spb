"""User constraints, not database identities, observations or executable SQL."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .device_price_quote import DevicePriceSpecificationFilter
from .product_price_execution import PriceSelectionReference
from .product_price import PriceText


class PriceUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit: Literal["KG", "G", "JIN", "EACH", "UNSUPPORTED"]
    quantity: Decimal = Field(default=Decimal(1), gt=0, le=100000, allow_inf_nan=False)
    raw_text: PriceText


class PriceTimeConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["latest", "today", "unsupported"]
    raw_text: PriceText


class UnknownPriceConditions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["unknown"] = "unknown"
    subject_text: PriceText | None = None


class DevicePriceConditions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["device"] = "device"
    brand: PriceText | None = None
    product_text: PriceText | None = None
    specification: DevicePriceSpecificationFilter = Field(
        default_factory=DevicePriceSpecificationFilter
    )


class FreshPriceConditions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["fresh"] = "fresh"
    commodity: PriceText | None = None
    variety: PriceText | None = None
    # Source geography is not the logistics demo's administrative-code directory.
    region_text: PriceText | None = None
    market_text: PriceText | None = None
    price_nature: Literal["RETAIL_AVERAGE", "WHOLESALE_AVERAGE"] | None = None
    source_scope: Literal["single_scope", "separate_sources"] | None = None
    requested_unit: PriceUnitRequest | None = None


PriceConditions = Annotated[
    UnknownPriceConditions | DevicePriceConditions | FreshPriceConditions,
    Field(discriminator="kind"),
]


class ProductPriceSlots(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Literal["product_price"] = "product_price"
    conditions: PriceConditions = Field(default_factory=UnknownPriceConditions)
    time: PriceTimeConstraint | None = None


def missing_product_price_slots(slots: ProductPriceSlots) -> list[str]:
    conditions = slots.conditions
    if isinstance(conditions, UnknownPriceConditions):
        return ["conditions.kind"]
    if isinstance(conditions, DevicePriceConditions):
        return [] if conditions.product_text else ["conditions.product_text"]
    missing = [] if conditions.commodity else ["conditions.commodity"]
    if conditions.source_scope != "separate_sources":
        if not conditions.region_text:
            missing.append("conditions.region_text")
        if not conditions.price_nature:
            missing.append("conditions.price_nature")
    return missing


class ProductPriceCommand(BaseModel):
    """Validated semantic conditions and an optional server-resolved D3 reference.

    No original question as an executable parameter, no model-generated IDs.
    `today` remains an explicit constraint for the result-time validator; it
    never promises the latest available observation was published today.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Literal["product_price"] = "product_price"
    selection: PriceSelectionReference | None = None
    conditions: Annotated[
        DevicePriceConditions | FreshPriceConditions, Field(discriminator="kind")
    ]
    time: PriceTimeConstraint = Field(
        default_factory=lambda: PriceTimeConstraint(
            kind="latest", raw_text="最新可用观察"
        )
    )

    @model_validator(mode="after")
    def executable_constraints(self) -> ProductPriceCommand:
        if missing_product_price_slots(
            ProductPriceSlots(conditions=self.conditions, time=self.time)
        ):
            raise ValueError("product price command requires complete conditions")
        if self.time.kind == "unsupported":
            raise ValueError("historical dates and trends are not supported")
        if self.selection is not None and self.selection.kind != self.conditions.kind:
            raise ValueError("selected identity must belong to the requested category")
        if isinstance(self.conditions, FreshPriceConditions):
            unit = self.conditions.requested_unit
            if unit is not None and unit.unit == "UNSUPPORTED":
                raise ValueError("unsupported price unit must not be silently dropped")
        return self
