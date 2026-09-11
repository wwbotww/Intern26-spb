"""Stage A's same synthetic evidence represented as V2 consumer facts.

This helper does not emulate SQL or certify source provenance. The published A
fixture and its manifest remain unchanged; IDs introduced here are visibly local
database identities, never substitutes for official product or SKU identifiers.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from spb_assistant_api.domain.exceptions import PriceRepositoryUnavailableError
from spb_assistant_api.domain.product_price import ProductPriceReadRecord
from spb_assistant_api.domain.product_price_query import (
    DevicePriceReadQuery,
    PriceReadBatch,
    ProductPriceReadQuery,
)


BASELINE_PATH = Path(__file__).parent / "fixtures/product_price/legacy_device_baseline.json"
DEVICE_BASELINE = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
READ_AT = datetime(2026, 8, 3, tzinfo=timezone.utc)
BRAND_CHANNEL_IDS = {brand: index for index, brand in enumerate(
    ("APPLE", "HUAWEI", "XIAOMI", "OPPO", "VIVO"), start=101,
)}


def baseline_record_values(key: str) -> dict[str, Any]:
    return {**DEVICE_BASELINE["record_defaults"], **DEVICE_BASELINE["records"][key]}


# Multiple SKU records of one official product deliberately share one catalog
# identity. Input ordering, titles and prices never change their product group.
PRODUCT_IDS = {
    identity: index for index, identity in enumerate(sorted({
        (baseline_record_values(key)["brand_code"], baseline_record_values(key)["official_product_id"])
        for key in DEVICE_BASELINE["records"]
    }), start=201)
}


def _variant_key(values: dict[str, Any]) -> tuple[Any, ...]:
    return (
        values["brand_code"], values["official_product_id"],
        *(values[field] or None for field in ("capacity", "memory", "color", "connectivity", "size")),
    )


# The A fixtures intentionally contain several source SKUs with identical proven
# configurations and differing availability. They may share a canonical variant;
# inventing an extra dimension solely to separate them would change evidence.
VARIANT_IDS = {
    identity: index for index, identity in enumerate(dict.fromkeys(
        _variant_key(baseline_record_values(key)) for key in DEVICE_BASELINE["records"]
    ), start=3001)
}


def baseline_v2_record(key: str) -> ProductPriceReadRecord:
    values = baseline_record_values(key)
    source_id = BRAND_CHANNEL_IDS[values["brand_code"]]
    local_id = values["offer_id"]
    listing_id, revision_id = 1000 + local_id, 2000 + local_id
    variant_id = VARIANT_IDS[_variant_key(values)]
    observation_id, crawl_id = 4000 + local_id, 5000 + local_id
    observed = datetime.fromisoformat(values["observed_at"])
    region = {"scope": "NATIONAL", "code": "CN"}
    # Only the A record fields are evidence. Do not infer a specification from
    # sku_name, title, official identifier, or the test question.
    specification = {field: values[field] or None for field in (
        "capacity", "memory", "color", "connectivity", "size",
    )}
    return ProductPriceReadRecord.model_validate({
        "read_at": READ_AT,
        "listing": {
            "source_listing_id": listing_id,
            "listing_revision_id": revision_id,
            "revision_source_listing_id": listing_id,
            "current_revision_id": revision_id,
            "price_nature": "RETAIL_OFFER",
            "revision_quality_status": "ACCEPTED",
            "base_quantity_value": Decimal("1"),
            "base_unit": "PIECE",
            "source": {
                "source_channel_id": source_id,
                "merchant_source_channel_id": source_id,
                "channel_code": f"{values['brand_code']}_CN_WEB",
                "channel_name": values["channel_name"],
                "source_type": "OFFICIAL_MALL",
                "business_mode": "SELF_OPERATED",
                "seller_type": "BRAND_OFFICIAL",
                "verification_status": "VERIFIED",
                # Both inputs are already in A's evidence. Preserve its explicit
                # URL fallback without pretending example.test passes the SQL
                # adapter's independently tested official-source allowlist.
                "source_url": values["source_url"] or values["official_product_url"],
            },
            "identity": {
                "kind": "device",
                "category_code": "LAPTOP" if key == "macbook_memory_16" else "PHONE",
                "brand_code": values["brand_code"],
                "brand_name": values["brand_name"],
                "catalog_item_id": PRODUCT_IDS[(values["brand_code"], values["official_product_id"])],
                "item_variant_id": variant_id,
                "product_name": values["product_name"],
                "series_name": values["series_name"] or None,
                "model_number": values["model_number"] or None,
                "official_product_id": values["official_product_id"],
                "official_sku_id": values["official_sku_id"] or None,
                "specification": specification,
                "match": {
                    "listing_revision_id": revision_id,
                    "item_variant_id": variant_id,
                    "status": "ACCEPTED",
                    "effective_from": observed,
                    "effective_to": None,
                },
            },
        },
        "pointer": {
            "source_listing_id": listing_id,
            "listing_revision_id": revision_id,
            "price_observation_id": observation_id,
            "region": region,
            "observed_at": observed,
        },
        "observation": {
            "kind": "priced",
            "observation_id": observation_id,
            "source_listing_id": listing_id,
            "listing_revision_id": revision_id,
            "crawl_record_id": crawl_id,
            "region": region,
            "currency": values["currency"],
            "observed_at": observed,
            "time_precision": "INSTANT",
            "quality_status": "ACCEPTED",
            "price_nature": "RETAIL_OFFER",
            "price_type": "DIRECT_UNCONDITIONAL",
            "pricing_basis": "PACKAGE_TOTAL",
            "availability": values["availability"],
            "current_price": Decimal(values["current_price"]),
            "original_price": (
                Decimal(values["original_price"]) if values["original_price"] is not None else None
            ),
            "original_price_type": values["original_price_type"],
            "unit_price": None,
            "fee_status": "ITEM_ONLY",
        },
        "last_known_price": None,
    })


class SyntheticV2DeviceRepository:
    """No V1 repository, DB, HTTP or model access; narrow V2 read contract only."""

    def __init__(self, case: dict[str, Any]) -> None:
        self.case = case
        self.queries: list[ProductPriceReadQuery] = []

    def readiness(self) -> str:
        return "ready"

    async def search(self, query: ProductPriceReadQuery) -> PriceReadBatch:
        self.queries.append(query)
        if self.case.get("repository_unavailable"):
            raise PriceRepositoryUnavailableError("synthetic V2 unavailable")
        assert isinstance(query, DevicePriceReadQuery)
        candidates = [baseline_v2_record(key) for key in self.case["records"]]
        # The real V2 repository applies these hard scope constraints. Returning
        # another brand/category is a B contract error, not a ranking candidate.
        # Do not filter model/family/specification: those are what C must prove.
        candidates = [record for record in candidates if (
            query.brand_code is None or record.listing.identity.brand_code == query.brand_code
        ) and (
            query.category_code is None or record.listing.identity.category_code == query.category_code
        )]
        counts: Counter[int] = Counter()
        selected = []
        for record in candidates:
            product_id = record.listing.identity.catalog_item_id
            if product_id not in counts and len(counts) >= query.product_limit:
                continue
            if counts[product_id] >= query.per_product_limit:
                continue
            counts[product_id] += 1
            selected.append(record)
        return PriceReadBatch(records=tuple(selected), truncated=len(selected) < len(candidates))
