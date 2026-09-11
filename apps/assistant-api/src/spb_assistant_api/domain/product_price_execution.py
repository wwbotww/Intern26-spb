"""Internal price execution/checkpoint models; never serialize these as public DTOs."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .product_price import PriceId, PriceRegion, PriceText, ProductPriceReadRecord
from .product_price_query import ProductPriceCandidateFact


Fingerprint = Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]
CandidateToken = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{43}$")
]


class PriceSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_token: CandidateToken


class PriceSelectionReference(BaseModel):
    """Server-resolved identity, not an ID supplied by the model/browser."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["device", "fresh"]
    source_listing_id: PriceId
    region: PriceRegion
    identity_fingerprint: Fingerprint

    @classmethod
    def from_record(cls, record: ProductPriceReadRecord) -> PriceSelectionReference:
        identity = record.listing.identity.model_dump(mode="json", exclude={"match"})
        # Revision changes conservatively invalidate a choice. Observation, price,
        # availability and source URL may change without substituting the identity.
        source = record.listing.source
        value = {
            "listing": record.listing.source_listing_id,
            "revision": record.listing.listing_revision_id,
            "identity": identity,
            "region": record.pointer.region.model_dump(),
            "nature": record.listing.price_nature,
            "channel": (source.source_channel_id, source.channel_code),
            "base": (
                record.listing.base_unit,
                str(record.listing.base_quantity_value.normalize()),
            ),
        }
        digest = hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return cls(
            kind=record.listing.identity.kind,
            source_listing_id=record.listing.source_listing_id,
            region=record.pointer.region,
            identity_fingerprint=f"sha256:{digest}",
        )


class PriceCandidateChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token: CandidateToken
    label: PriceText
    reference: PriceSelectionReference


class PriceCandidateSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: PriceText
    conversation_id: PriceText
    query_id: PriceText
    constraint_fingerprint: Fingerprint
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    choices: tuple[PriceCandidateChoice, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_set(self):
        if (
            self.expires_at <= self.issued_at
            or (self.expires_at - self.issued_at).total_seconds() > 600
        ):
            raise ValueError("price choices must expire within ten minutes")
        if len({item.token for item in self.choices}) != len(self.choices):
            raise ValueError("candidate tokens must be unique")
        if len({item.reference.model_dump_json() for item in self.choices}) != len(
            self.choices
        ):
            raise ValueError("candidate identities must be unique")
        return self


class ProductPriceData(BaseModel):
    """Validated execution facts for Tool receipts; D4 owns the public whitelist."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["product_price"] = "product_price"
    mode: Literal["candidates", "quote"]
    command_fingerprint: Fingerprint
    facts: tuple[ProductPriceCandidateFact, ...] = Field(min_length=1, max_length=100)
    truncated: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def unique_facts(self):
        keys = [
            (
                fact.record.pointer.source_listing_id,
                fact.record.pointer.region.scope,
                fact.record.pointer.region.code,
            )
            for fact in self.facts
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("price results cannot duplicate a current identity")
        return self


class ProductPriceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["quote", "candidates", "need_more_info", "no_match"]
    facts: tuple[ProductPriceCandidateFact, ...] = Field(default=(), max_length=100)
    truncated: bool = False
    reason_code: str = ""
    missing_slots: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistent_status(self):
        if bool(self.facts) != (self.status in {"quote", "candidates"}):
            raise ValueError("quote status must agree with facts")
        if self.status == "need_more_info" and not self.missing_slots:
            raise ValueError("result clarification needs actionable slots")
        return self
