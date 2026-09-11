"""Consumer-owned V2 price facts, not SQL rows or public Agent DTOs.

The first read contract covers official devices and government fresh averages.
It is exercised offline before a Repository or an Agent capability is wired in.
Unknown producer fields must be reviewed at the adapter boundary, not inferred.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)


def _exact_decimal(value: object) -> object:
    if isinstance(value, (bool, float)):
        raise ValueError("price facts require exact decimals, not floats or booleans")
    return value


PriceAmount = Annotated[
    Decimal,
    BeforeValidator(_exact_decimal),
    Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False),
]
UnitPriceAmount = Annotated[
    Decimal,
    BeforeValidator(_exact_decimal),
    Field(gt=0, max_digits=20, decimal_places=6, allow_inf_nan=False),
]
Quantity = Annotated[
    Decimal,
    BeforeValidator(_exact_decimal),
    Field(gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False),
]
PriceId = Annotated[int, Field(strict=True, gt=0)]
PriceText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=255)
]
PriceCode = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Z0-9_.-]{1,64}$")
]
PriceNature = Literal[
    "RETAIL_OFFER", "WHOLESALE_OFFER", "RETAIL_AVERAGE", "WHOLESALE_AVERAGE", "MARKET_AVERAGE"
]
PriceAvailability = Literal[
    "ON_SALE", "OUT_OF_STOCK", "RESERVATION", "PRE_SALE", "COMING_SOON", "OFF_SHELF", "UNKNOWN"
]


class _PriceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PriceRegion(_PriceModel):
    scope: Literal["NATIONAL", "PROVINCE", "CITY", "DISTRICT", "DELIVERY_ZONE"]
    code: PriceCode

    @model_validator(mode="after")
    def national_code(self) -> PriceRegion:
        if self.code in {"UNKNOWN", "MULTI"}:
            raise ValueError("accepted prices cannot use placeholder region codes")
        if self.scope == "NATIONAL" and self.code != "CN":
            raise ValueError("national prices require region CN")
        if self.scope != "NATIONAL" and self.code == "CN":
            raise ValueError("region CN is national, not a province or city")
        return self


class PriceSource(_PriceModel):
    source_channel_id: PriceId
    merchant_source_channel_id: PriceId
    channel_code: PriceCode
    channel_name: PriceText
    source_type: Literal["OFFICIAL_MALL", "PUBLIC_DATA"]
    business_mode: Literal["SELF_OPERATED", "WHOLESALE"]
    seller_type: Literal["BRAND_OFFICIAL", "PUBLIC_MARKET", "UNKNOWN"] | None = None
    verification_status: Literal["VERIFIED", "UNVERIFIED", "UNKNOWN"] | None = None
    source_url: HttpUrl

    @model_validator(mode="after")
    def no_url_credentials(self) -> PriceSource:
        if self.source_channel_id != self.merchant_source_channel_id:
            raise ValueError("listing source and merchant must belong to the same channel")
        if self.source_url.username is not None or self.source_url.password is not None:
            raise ValueError("source URLs cannot contain credentials")
        return self


class DevicePriceSpecification(_PriceModel):
    color: PriceText | None = None
    capacity: PriceText | None = None
    memory: PriceText | None = None
    connectivity: PriceText | None = None
    size: PriceText | None = None
    edition: PriceText | None = None
    manufacturer_part_number: PriceText | None = None
    attributes: dict[PriceText, PriceText] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def proven_configuration(self) -> DevicePriceSpecification:
        if not any(self.model_dump(exclude={"attributes"}).values()) and not self.attributes:
            raise ValueError("device specifications require at least one proven dimension")
        mutable_fields = {
            "price", "current_price", "original_price", "availability", "stock",
            "title", "name", "observed_at", "fetched_at", "url",
        }
        if any(key.lower() in mutable_fields for key in self.attributes):
            raise ValueError("quote fields cannot be device identity dimensions")
        return self


class DevicePriceMatch(_PriceModel):
    listing_revision_id: PriceId
    item_variant_id: PriceId
    status: Literal["ACCEPTED"]
    effective_from: AwareDatetime
    effective_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> DevicePriceMatch:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("device match has a reversed validity interval")
        return self


class DevicePriceIdentity(_PriceModel):
    kind: Literal["device"]
    category_code: Literal["PHONE", "TABLET", "LAPTOP", "DESKTOP", "WATCH"]
    brand_code: Literal["APPLE", "HUAWEI", "XIAOMI", "OPPO", "VIVO"]
    brand_name: PriceText
    catalog_item_id: PriceId
    item_variant_id: PriceId
    product_name: PriceText
    series_name: PriceText | None = None
    model_number: PriceText | None = None
    official_product_id: PriceText
    official_sku_id: PriceText | None = None
    specification: DevicePriceSpecification
    match: DevicePriceMatch


class FreshPriceIdentity(_PriceModel):
    kind: Literal["fresh"]
    category_code: Literal["FRESH_MONITORED_COMMODITY"]
    commodity_code: PriceCode
    commodity_name: PriceText
    commodity_group: Literal["VEGETABLE", "FRUIT", "MEAT_EGG"]
    source_specification: PriceText
    quoted_unit: Literal["CNY_PER_500G", "CNY_PER_KG"]
    source_market_id: PriceText | None = None
    source_market_name: PriceText | None = None

    @model_validator(mode="after")
    def market_identity_pair(self) -> FreshPriceIdentity:
        if (self.source_market_id is None) != (self.source_market_name is None):
            raise ValueError("supplied market ID and name must remain paired")
        return self


PriceIdentity = Annotated[DevicePriceIdentity | FreshPriceIdentity, Field(discriminator="kind")]


class UnitPrice(_PriceModel):
    amount: UnitPriceAmount
    unit: Literal["CNY_PER_PIECE", "CNY_PER_KG"]


class _Observation(_PriceModel):
    observation_id: PriceId
    source_listing_id: PriceId
    listing_revision_id: PriceId
    crawl_record_id: PriceId
    region: PriceRegion
    currency: Literal["CNY"]
    observed_at: AwareDatetime
    time_precision: Literal["DAY", "INSTANT", "UNKNOWN"]
    quality_status: Literal["ACCEPTED"]


class PricedObservation(_Observation):
    kind: Literal["priced"]
    price_nature: PriceNature
    price_type: Literal["DIRECT_UNCONDITIONAL", "PUBLISHED_VALUE"]
    pricing_basis: Literal["PACKAGE_TOTAL", "UNIT_QUOTED"]
    availability: PriceAvailability
    current_price: PriceAmount
    original_price: PriceAmount | None = None
    original_price_type: Literal["NONE", "CROSSED_OUT", "MSRP", "EXPLICIT_ORIGINAL"] = "NONE"
    unit_price: UnitPrice | None = None
    fee_status: Literal["ITEM_ONLY", "SEPARATE_FEES_EXCLUDED", "NOT_APPLICABLE"]

    @model_validator(mode="after")
    def exact_price_semantics(self) -> PricedObservation:
        if (self.original_price is None) != (self.original_price_type == "NONE"):
            raise ValueError("original amount and original-price type must agree")
        if self.pricing_basis == "UNIT_QUOTED" and self.unit_price is None:
            raise ValueError("unit-quoted prices require a separate normalized unit price")
        if self.price_nature in {"RETAIL_OFFER", "WHOLESALE_OFFER"}:
            if self.price_type != "DIRECT_UNCONDITIONAL" or self.fee_status not in {
                "ITEM_ONLY", "SEPARATE_FEES_EXCLUDED",
            }:
                raise ValueError("offer prices must be unconditional with separable fees")
        elif self.price_type != "PUBLISHED_VALUE" or self.fee_status != "NOT_APPLICABLE":
            raise ValueError("average prices must be published values, not offers")
        return self


class AvailabilityObservation(_Observation):
    kind: Literal["availability_only"]
    price_nature: Literal["RETAIL_OFFER"]
    price_type: Literal["AVAILABILITY_ONLY"]
    pricing_basis: Literal["UNKNOWN"]
    availability: Literal["OFF_SHELF", "OUT_OF_STOCK", "COMING_SOON"]
    current_price: None = None
    original_price: None = None
    original_price_type: Literal["NONE"] = "NONE"
    unit_price: None = None
    fee_status: Literal["NOT_APPLICABLE"]
    promotion_label: None = None


PriceObservation = Annotated[
    PricedObservation | AvailabilityObservation, Field(discriminator="kind")
]


class PriceCurrentPointer(_PriceModel):
    source_listing_id: PriceId
    listing_revision_id: PriceId
    price_observation_id: PriceId
    region: PriceRegion
    observed_at: AwareDatetime


class PriceListing(_PriceModel):
    source_listing_id: PriceId
    listing_revision_id: PriceId
    revision_source_listing_id: PriceId
    current_revision_id: PriceId
    price_nature: PriceNature
    revision_quality_status: Literal["ACCEPTED"]
    base_quantity_value: Quantity
    base_unit: Literal["PIECE", "KG"]
    source: PriceSource
    identity: PriceIdentity


class ProductPriceReadRecord(_PriceModel):
    """One current fact and its identity, validated before matching or rendering.

    The adapter still has to prove joins and source policy in SQL. A constructed
    Python object alone is not evidence that the producer wrote trustworthy data.
    """

    read_at: AwareDatetime
    listing: PriceListing
    pointer: PriceCurrentPointer
    observation: PriceObservation
    last_known_price: PricedObservation | None = None

    @model_validator(mode="after")
    def coherent_current_record(self) -> ProductPriceReadRecord:
        listing, pointer, observation = self.listing, self.pointer, self.observation
        if observation.observed_at > self.read_at:
            raise ValueError("current observations cannot be in the future at read completion")
        if not (
            listing.source_listing_id == listing.revision_source_listing_id
            == pointer.source_listing_id == observation.source_listing_id
            and listing.listing_revision_id == listing.current_revision_id
            == pointer.listing_revision_id == observation.listing_revision_id
            and pointer.price_observation_id == observation.observation_id
            and pointer.region == observation.region
            and pointer.observed_at == observation.observed_at
        ):
            raise ValueError("current pointer, listing, revision and observation must agree")
        self._validate_scope(observation)
        historical = self.last_known_price
        if historical is not None:
            if observation.kind != "availability_only":
                raise ValueError("last-known price is only a supplement to amountless current state")
            if not (
                historical.source_listing_id == observation.source_listing_id
                and historical.listing_revision_id == observation.listing_revision_id
                and historical.region == observation.region
                and historical.currency == observation.currency
                and historical.observed_at < observation.observed_at
                and historical.observation_id != observation.observation_id
            ):
                raise ValueError("last-known price must be older and from the same identity and region")
            self._validate_scope(historical)
        return self

    def _validate_scope(self, observation: PricedObservation | AvailabilityObservation) -> None:
        listing = self.listing
        identity, source = listing.identity, listing.source
        if observation.price_nature != listing.price_nature:
            raise ValueError("listing and observation price nature must agree")
        if identity.kind == "device":
            match = identity.match
            if not (
                match.listing_revision_id == listing.listing_revision_id
                and match.item_variant_id == identity.item_variant_id
                and match.effective_from <= self.read_at
                and (match.effective_to is None or self.read_at < match.effective_to)
            ):
                raise ValueError("device match must bind this revision and variant at read time")
            if not (
                source.channel_code == f"{identity.brand_code}_CN_WEB"
                and source.source_type == "OFFICIAL_MALL"
                and source.business_mode == "SELF_OPERATED"
                and source.seller_type == "BRAND_OFFICIAL"
                and source.verification_status == "VERIFIED"
                and observation.price_nature == "RETAIL_OFFER"
                and observation.region == PriceRegion(scope="NATIONAL", code="CN")
                and listing.base_unit == "PIECE"
                and listing.base_quantity_value == 1
            ):
                raise ValueError("device facts require an official single-device national source")
            if observation.kind == "priced" and observation.pricing_basis != "PACKAGE_TOTAL":
                raise ValueError("device amounts must quote the complete configuration")
            if (
                observation.kind == "priced"
                and observation.original_price is not None
                and observation.original_price < observation.current_price
            ):
                raise ValueError("official retail original price cannot be below the current price")
        else:
            profiles = {
                "SH_FGW_FRESH_RETAIL": ("RETAIL_AVERAGE", "SELF_OPERATED", "CNY_PER_500G"),
                "MOFCOM_FRESH_WHOLESALE": ("WHOLESALE_AVERAGE", "WHOLESALE", "CNY_PER_KG"),
            }
            if not (
                profiles.get(source.channel_code)
                == (observation.price_nature, source.business_mode, identity.quoted_unit)
                and source.source_type == "PUBLIC_DATA"
                and source.seller_type == "PUBLIC_MARKET"
                and source.verification_status == "VERIFIED"
                and observation.kind == "priced"
                and observation.pricing_basis == "UNIT_QUOTED"
                and observation.availability == "UNKNOWN"
                and observation.original_price is None
                and observation.original_price_type == "NONE"
                and observation.time_precision == "DAY"
                and listing.base_unit == "KG"
                and listing.base_quantity_value
                == (Decimal("0.5") if identity.quoted_unit == "CNY_PER_500G" else Decimal("1"))
            ):
                raise ValueError("fresh facts must preserve the supported publication and quoted unit")
            if source.channel_code == "SH_FGW_FRESH_RETAIL":
                if observation.region != PriceRegion(scope="CITY", code="310100"):
                    raise ValueError("Shanghai published prices require CITY/310100")
            elif observation.region.scope != "PROVINCE":
                raise ValueError("MOFCOM publication rows require their source province code")
            source_day = observation.observed_at.astimezone(ZoneInfo("Asia/Shanghai"))
            if (source_day.hour, source_day.minute, source_day.second, source_day.microsecond) != (0, 0, 0, 0):
                raise ValueError("government DAY observations must preserve the Shanghai source midnight")
        if observation.kind == "priced" and observation.unit_price is not None:
            unit_price = observation.unit_price
            # This first scope has only one piece, one kg or half a kg. No
            # unknown-unit conversion or rounding of a contradictory fact.
            with localcontext() as context:
                context.prec = 40
                expected = observation.current_price / listing.base_quantity_value
            if unit_price.unit != f"CNY_PER_{listing.base_unit}" or unit_price.amount != expected:
                raise ValueError("normalized unit price contradicts the original quote or quantity")
