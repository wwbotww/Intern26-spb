"""Public price whitelist. Never export internal read records or selection references."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)

from ..domain.product_price_execution import ProductPriceData

Amount = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[0-9]{1,18}\.[0-9]{2,6}$")
]
Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PriceCandidateOption(PublicModel):
    candidate_token: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]
    label: Text
    expires_at: AwareDatetime


class ProductDeviceIdentity(PublicModel):
    kind: Literal["device"]
    brand: Text
    product_name: Text
    specification: dict[str, Text] = Field(max_length=23)


class ProductFreshIdentity(PublicModel):
    kind: Literal["fresh"]
    commodity_name: Text
    source_specification: Text
    market_name: Text | None = None


class ProductUnitPrice(PublicModel):
    amount: Amount
    unit: Literal["CNY_PER_PIECE", "CNY_PER_KG"]


class ProductQuote(PublicModel):
    kind: Literal["priced", "availability_only"]
    currency: Literal["CNY"] = "CNY"
    current_price: Amount | None
    original_price: Amount | None
    original_price_type: Literal["NONE", "CROSSED_OUT", "MSRP", "EXPLICIT_ORIGINAL"]
    availability: Literal[
        "ON_SALE",
        "OUT_OF_STOCK",
        "RESERVATION",
        "PRE_SALE",
        "COMING_SOON",
        "OFF_SHELF",
        "UNKNOWN",
    ]
    price_nature: Literal["RETAIL_OFFER", "RETAIL_AVERAGE", "WHOLESALE_AVERAGE"]
    quoted_unit: Literal["CNY_PER_PIECE", "CNY_PER_500G", "CNY_PER_KG"]
    unit_price: ProductUnitPrice | None
    observed_at: AwareDatetime
    time_precision: Literal["DAY", "INSTANT", "UNKNOWN"]

    @model_validator(mode="after")
    def coherent(self):
        if (self.kind == "priced") != (self.current_price is not None):
            raise ValueError("amountless state is not a priced quotation")
        if self.current_price is not None and Decimal(self.current_price) <= 0:
            raise ValueError("prices must be positive")
        if (self.original_price is None) != (self.original_price_type == "NONE"):
            raise ValueError("original price type mismatch")
        if self.original_price is not None and Decimal(self.original_price) <= 0:
            raise ValueError("original price must be positive")
        if self.kind == "availability_only" and (
            self.unit_price is not None
            or self.original_price is not None
            or self.availability not in {"OUT_OF_STOCK", "OFF_SHELF", "COMING_SOON"}
        ):
            raise ValueError("invalid amountless state")
        if self.unit_price is not None and self.current_price is not None:
            expected = Decimal(self.current_price) * (
                2 if self.quoted_unit == "CNY_PER_500G" else 1
            )
            expected_unit = (
                "CNY_PER_PIECE" if self.quoted_unit == "CNY_PER_PIECE" else "CNY_PER_KG"
            )
            if (
                self.unit_price.unit != expected_unit
                or Decimal(self.unit_price.amount) != expected
            ):
                raise ValueError("normalized unit contradicts original quote")
        return self


class ProductSource(PublicModel):
    name: Text
    profile: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9_.-]{1,64}$")]
    url: HttpUrl

    @model_validator(mode="after")
    def no_credentials(self):
        if self.url.username is not None or self.url.password is not None:
            raise ValueError("public source cannot contain credentials")
        return self


class ProductRegion(PublicModel):
    scope: Literal["NATIONAL", "PROVINCE", "CITY", "DISTRICT", "DELIVERY_ZONE"]
    code: Text


class ProductPriceItem(PublicModel):
    identity: Annotated[
        ProductDeviceIdentity | ProductFreshIdentity, Field(discriminator="kind")
    ]
    quote: ProductQuote
    source: ProductSource
    region: ProductRegion
    queried_at: AwareDatetime
    freshness: Literal["unknown", "fresh", "stale"]
    last_known_price: ProductQuote | None = None

    @model_validator(mode="after")
    def coherent_item(self):
        if self.quote.observed_at > self.queried_at:
            raise ValueError("observation cannot follow query")
        if self.identity.kind == "fresh" and (
            self.quote.quoted_unit == "CNY_PER_PIECE"
            or self.quote.price_nature == "RETAIL_OFFER"
        ):
            raise ValueError("fresh identity cannot use device quotation")
        if self.identity.kind == "device" and (
            self.quote.quoted_unit != "CNY_PER_PIECE"
            or self.quote.price_nature != "RETAIL_OFFER"
        ):
            raise ValueError("device quote must be per configuration")
        if (
            self.identity.kind == "fresh"
            and self.quote.kind == "priced"
            and self.quote.unit_price is None
        ):
            raise ValueError("fresh quote requires its normalized unit")
        if self.last_known_price is not None and (
            self.quote.kind != "availability_only"
            or self.last_known_price.kind != "priced"
            or self.last_known_price.observed_at >= self.quote.observed_at
        ):
            raise ValueError("historical price must be a separate older supplement")
        if self.last_known_price is not None and (
            self.last_known_price.quoted_unit != self.quote.quoted_unit
            or self.last_known_price.price_nature != self.quote.price_nature
        ):
            raise ValueError("history quotation basis must match current state")
        return self


class ProductPriceResponseData(PublicModel):
    type: Literal["product_price"] = "product_price"
    schema_version: Literal["1"] = "1"
    items: list[ProductPriceItem] = Field(min_length=1, max_length=100)
    truncated: bool

    @classmethod
    def from_domain(cls, data: ProductPriceData):
        if data.mode != "quote":
            raise ValueError("discovery is not a public final quotation")
        items = []
        for fact in data.facts:
            record = fact.record
            identity = record.listing.identity
            if identity.kind == "device":
                spec = identity.specification.model_dump(
                    exclude_none=True, exclude={"attributes"}
                )
                if set(spec) & set(identity.specification.attributes):
                    raise ValueError(
                        "attribute names cannot overwrite proven specification fields"
                    )
                spec.update(identity.specification.attributes)
                public_identity = ProductDeviceIdentity(
                    kind="device",
                    brand=identity.brand_name,
                    product_name=identity.product_name,
                    specification=spec,
                )
            else:
                public_identity = ProductFreshIdentity(
                    kind="fresh",
                    commodity_name=identity.commodity_name,
                    source_specification=identity.source_specification,
                    market_name=identity.source_market_name,
                )

            def quote(observation):
                return ProductQuote(
                    kind=observation.kind,
                    current_price=format(observation.current_price, ".2f")
                    if observation.current_price is not None
                    else None,
                    original_price=format(observation.original_price, ".2f")
                    if observation.original_price is not None
                    else None,
                    original_price_type=observation.original_price_type,
                    availability=observation.availability,
                    price_nature=observation.price_nature,
                    quoted_unit="CNY_PER_PIECE"
                    if identity.kind == "device"
                    else identity.quoted_unit,
                    unit_price=ProductUnitPrice(
                        amount=format(observation.unit_price.amount, ".6f"),
                        unit=observation.unit_price.unit,
                    )
                    if observation.unit_price
                    else None,
                    observed_at=observation.observed_at,
                    time_precision=observation.time_precision,
                )

            items.append(
                ProductPriceItem(
                    identity=public_identity,
                    quote=quote(record.observation),
                    source=ProductSource(
                        name=record.listing.source.channel_name,
                        profile=record.listing.source.channel_code,
                        url=record.listing.source.source_url,
                    ),
                    region=ProductRegion(
                        scope=record.pointer.region.scope,
                        code=record.pointer.region.code,
                    ),
                    queried_at=record.read_at,
                    freshness=fact.freshness.status,
                    last_known_price=quote(record.last_known_price)
                    if record.last_known_price
                    else None,
                )
            )
        return cls(items=items, truncated=data.truncated)
