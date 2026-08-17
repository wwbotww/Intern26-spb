from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DevicePriceSearchQuery:
    brand_code: str | None
    terms: tuple[str, ...]
    limit: int


@dataclass(frozen=True, slots=True)
class DevicePriceRecord:
    offer_id: int
    brand_code: str
    brand_name: str
    official_product_id: str
    product_name: str
    series_name: str
    model_number: str
    official_product_url: str
    official_sku_id: str
    sku_name: str
    color: str
    capacity: str
    memory: str
    connectivity: str
    size: str
    availability: str
    source_url: str
    channel_name: str
    currency: str
    original_price: Decimal | None
    original_price_type: str
    current_price: Decimal
    observed_at: datetime
