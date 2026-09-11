"""Internal device quotation results, independent of old HTTP price cards."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .product_price import PriceText
from .product_price_query import ProductPriceCandidateFact


class DevicePriceSpecificationFilter(BaseModel):
    """Explicit roles: storage must not match RAM, and unknown values are not guessed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capacity: PriceText | None = None
    memory: PriceText | None = None
    color: PriceText | None = None
    connectivity: PriceText | None = None
    size: PriceText | None = None
    edition: PriceText | None = None
    manufacturer_part_number: PriceText | None = None
    attributes: dict[PriceText, PriceText] = Field(default_factory=dict, max_length=16)


class DevicePriceQuoteCandidate(ProductPriceCandidateFact):
    match_score: float = Field(strict=True, ge=0, le=100)

    @model_validator(mode="after")
    def device_fact_only(self) -> DevicePriceQuoteCandidate:
        if self.record.listing.identity.kind != "device":
            raise ValueError("a device quote cannot contain another category's facts")
        return self


class DevicePriceQuoteResult(BaseModel):
    """Matched includes amountless states; no_match never means merely null price."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["matched", "need_more_info", "no_match"]
    candidates: tuple[DevicePriceQuoteCandidate, ...] = Field(default=(), max_length=100)
    reason_code: Literal[
        "", "missing_device_identity", "no_matching_device", "no_matching_specification",
        "price_candidates_incomplete",
    ] = ""
    truncated: bool = Field(default=False, strict=True)
    recall_truncated: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def result_consistency(self) -> DevicePriceQuoteResult:
        if (self.status == "matched") != bool(self.candidates):
            raise ValueError("matched quotations must contain current device facts")
        if self.recall_truncated and not self.truncated:
            raise ValueError("incomplete recall must remain visible in the result")
        if self.status == "no_match" and self.truncated:
            raise ValueError("incomplete candidates cannot prove no match")
        keys = [(item.record.pointer.source_listing_id, item.record.pointer.region) for item in self.candidates]
        if len(set(keys)) != len(keys):
            raise ValueError("a quotation cannot duplicate a current fact")
        return self
