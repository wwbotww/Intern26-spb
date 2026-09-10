"""Provider-neutral, explicit pricing conditions; no wire fields or secrets."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


PostageIdentifier = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_.-]{1,128}$")
]
PricingFingerprint = Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]
QuoteAmountKind = Literal["total", "standard", "customer"]
QuoteMoney = Annotated[
    Decimal, Field(ge=0, le=Decimal("999999999.99"), decimal_places=2, allow_inf_nan=False)
]


class PostagePricingContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_version: PostageIdentifier
    policy_fingerprint: PricingFingerprint
    product_code: PostageIdentifier
    origin_billing_id: PostageIdentifier
    destination_billing_id: PostageIdentifier
    weight_grams: int = Field(strict=True, gt=0, le=1_000_000)
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    amount_kind: QuoteAmountKind
    scope: Literal["domestic_actual_weight_no_extras"] = "domestic_actual_weight_no_extras"


class PostageFeeItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "registration", "insurance", "declared_value", "inspection", "customs",
        "fuel", "return_receipt", "password_delivery", "printing", "handling",
    ]
    amount: QuoteMoney
    included_in_amount: Literal["yes", "no", "unknown"] = "unknown"


class PostageQuoteBasis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    context: PostagePricingContext
    is_estimate: Literal[True] = True
    # Empty means no supplied breakdown, not that every fee is zero.
    fees: tuple[PostageFeeItem, ...] = ()

    @model_validator(mode="after")
    def unique_fees(self) -> "PostageQuoteBasis":
        if len({item.kind for item in self.fees}) != len(self.fees):
            raise ValueError("报价费用项不能重复")
        return self


class PostageSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["fake_gateway", "external_api"]
    source_name: PostageIdentifier
    profile: PostageIdentifier


def exact_weight_grams(value: Decimal, unit: str, *, maximum: int) -> int:
    """Exact conversion without Decimal context rounding or huge allocations."""
    if not isinstance(value, Decimal) or unit not in {"kg", "g"} or not value.is_finite() or value <= 0:
        raise ValueError("重量必须是有限正数并使用 kg 或 g")
    sign, digits, exponent = value.as_tuple()
    shifted = Decimal((sign, digits, exponent + (3 if unit == "kg" else 0)))
    if shifted > maximum:
        raise ValueError("重量超过本地询价上限")
    if shifted != shifted.to_integral_value():
        raise ValueError("重量必须能精确转换为整数克，不自动取整")
    return int(shifted)
