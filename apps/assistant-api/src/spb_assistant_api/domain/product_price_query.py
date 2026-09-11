"""Bounded read requests and candidate facts; not natural-language Agent commands."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints,
    TypeAdapter, field_validator, model_validator,
)

from .product_price import PriceCode, PriceRegion, PriceText, ProductPriceReadRecord


SearchTerm = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)
]


class _ReadQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    terms: tuple[SearchTerm, ...] = Field(min_length=1, max_length=8)

    @field_validator("terms")
    @classmethod
    def normalized_terms(cls, terms: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(term.lower() for term in terms))


class DevicePriceReadQuery(_ReadQuery):
    kind: Literal["device"] = "device"
    brand_code: Literal["APPLE", "HUAWEI", "XIAOMI", "OPPO", "VIVO"] | None = None
    category_code: Literal["PHONE", "TABLET", "LAPTOP", "DESKTOP", "WATCH"] | None = None
    product_limit: int = Field(default=10, strict=True, ge=1, le=20)
    per_product_limit: int = Field(default=20, strict=True, ge=1, le=50)


class FreshPriceReadQuery(_ReadQuery):
    kind: Literal["fresh"] = "fresh"
    commodity_code: PriceCode | None = None
    region: PriceRegion | None = None
    price_nature: Literal["RETAIL_AVERAGE", "WHOLESALE_AVERAGE"] | None = None
    market_id: PriceText | None = None
    listing_limit: int = Field(default=20, strict=True, ge=1, le=100)


ProductPriceReadQuery = Annotated[
    DevicePriceReadQuery | FreshPriceReadQuery, Field(discriminator="kind")
]
PRODUCT_PRICE_READ_QUERY = TypeAdapter(ProductPriceReadQuery)


class PriceReadBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    records: tuple[ProductPriceReadRecord, ...] = Field(default=(), max_length=1000)
    truncated: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def unique_current_facts(self) -> PriceReadBatch:
        keys = [
            (record.pointer.source_listing_id, record.pointer.region.scope, record.pointer.region.code)
            for record in self.records
        ]
        observations = [record.observation.observation_id for record in self.records]
        if len(set(keys)) != len(keys) or len(set(observations)) != len(observations):
            raise ValueError("a price batch cannot duplicate current facts or ambiguous joins")
        if self.truncated and not self.records:
            raise ValueError("an empty read batch cannot claim omitted matching candidates")
        return self


class PriceFreshness(BaseModel):
    """Source-configured observation age, not a promise that a price is still valid."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["unknown", "fresh", "stale"]
    observed_at: AwareDatetime
    evaluated_at: AwareDatetime
    max_age_seconds: int | None = Field(default=None, strict=True, gt=0, le=366 * 24 * 60 * 60)

    @model_validator(mode="after")
    def consistent_age(self) -> PriceFreshness:
        age = self.evaluated_at - self.observed_at
        if age < timedelta(0):
            raise ValueError("freshness cannot be evaluated before the observation")
        expected = "unknown" if self.max_age_seconds is None else (
            "fresh" if age <= timedelta(seconds=self.max_age_seconds) else "stale"
        )
        if self.status != expected:
            raise ValueError("freshness must agree with the explicit source age threshold")
        return self


class ProductPriceCandidateFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record: ProductPriceReadRecord
    freshness: PriceFreshness

    @model_validator(mode="after")
    def current_observation_age(self) -> ProductPriceCandidateFact:
        if (
            self.freshness.observed_at != self.record.observation.observed_at
            or self.freshness.evaluated_at != self.record.read_at
        ):
            raise ValueError("freshness must describe the current observation and its read time")
        return self


class ProductPriceQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["candidates", "no_match"]
    candidates: tuple[ProductPriceCandidateFact, ...] = ()
    truncated: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def candidate_status(self) -> ProductPriceQueryResult:
        if (self.status == "no_match") != (not self.candidates):
            raise ValueError("candidate status must agree with the returned read facts")
        if self.truncated and not self.candidates:
            raise ValueError("no-match results cannot be truncated")
        return self
