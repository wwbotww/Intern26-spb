"""Strict projection boundary for the locally frozen V2 consumption contract.

These are SQL rows, not API DTOs. Never repair inconsistent producer evidence by
dropping a null companion, changing an amount, or guessing a source profile.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ..domain.exceptions import ProductPriceContractError
from ..domain.product_price import ProductPriceReadRecord


# A source-controlled publishing policy, not permission to fetch these URLs.
# Database allowed_domains may narrow it, never expand it.
SOURCE_DOMAINS: dict[str, frozenset[str]] = {
    "APPLE_CN_WEB": frozenset({"www.apple.com.cn"}),
    "HUAWEI_CN_WEB": frozenset({
        "www.vmall.com", "m.vmall.com", "item.vmall.com", "openapi.vmall.com",
    }),
    "XIAOMI_CN_WEB": frozenset({"www.mi.com"}),
    "OPPO_CN_WEB": frozenset({"www.opposhop.cn"}),
    "VIVO_CN_WEB": frozenset({"shop.vivo.com.cn"}),
    "SH_FGW_FRESH_RETAIL": frozenset({"fgw.sh.gov.cn"}),
    "MOFCOM_FRESH_WHOLESALE": frozenset({"cif.mofcom.gov.cn"}),
}


def _json(value: object, expected: type) -> Any:
    if isinstance(value, (str, bytes)):
        value = json.loads(value)
    if not isinstance(value, expected):
        raise ValueError("invalid JSON shape")
    return value


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("SQL observation timestamps must be datetimes")
    # The pool explicitly uses UTC. SQL DATETIME is intentionally timezone-less.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source_url(value: object, allowed: set[str]) -> str:
    if not isinstance(value, str) or any(ord(char) < 33 for char in value):
        raise ValueError("invalid source URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or parsed.hostname not in allowed
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 80, 443}
    ):
        raise ValueError("source URL outside publishing policy")
    return value


def _record(row: Mapping[str, Any], *, kind: str, read_at: datetime) -> ProductPriceReadRecord:
    attrs = _json(row["source_attributes"], dict)
    normalized = _json(row["normalized_attributes"], dict)
    domains = _json(row["allowed_domains"], list)
    if not domains or any(not isinstance(domain, str) for domain in domains):
        raise ValueError("invalid source allowlist")
    channel = row["channel_code"]
    allowed = set(domains) & SOURCE_DOMAINS.get(channel, frozenset())
    _source_url(row["canonical_url"], allowed)
    _source_url(row["crawl_request_url"], allowed)
    # Listing discovery metadata is mutable. Cite the selected observation's
    # own fetch evidence, not a URL refreshed by a later unsuccessful crawl.
    source_url = _source_url(row["crawl_final_url"], allowed)
    if not (
        row["source_channel_id"] == row["merchant_source_channel_id"]
        == row["crawl_run_source_channel_id"]
        and row["merchant_id"] == row["listing_merchant_id"]
        and row["crawl_id"] == row["crawl_record_id"]
        and row["crawl_run_id"] == row["record_crawl_run_id"]
        and row["crawl_source_listing_id"] in {None, row["source_listing_id"]}
        and row["crawl_fetch_status"] == row["crawl_parse_status"] == "SUCCEEDED"
        and row["source_currency"] == "CNY"
        and row["revision_rejection_code"] is None
        and row["observation_rejection_code"] is None
    ):
        raise ValueError("inconsistent source evidence")
    if (row["unit_price"] is None) != (row["unit_price_unit"] is None):
        raise ValueError("unit-price amount and unit must remain paired")
    if row["base_quantity_min"] is not None or row["base_quantity_max"] is not None:
        raise ValueError("first consumer scope requires an exact quantity")

    if kind == "device":
        variant_attrs = _json(row["variant_attributes"], dict)
        if not (
            row["normalizer_version"] == "electronic-device@1"
            and row["attribute_profile_code"] == "electronic-device"
            and row["attribute_profile_version"] == "1"
            and row["item_type"] == "MODEL"
            and row["item_variant_id"] == row["match_item_variant_id"]
            and row["catalog_item_id"] == row["variant_catalog_item_id"]
            and row["brand_id"] == row["item_brand_id"]
            and row["category_id"] == row["item_category_id"]
            and row["match_count"] == 1
            and variant_attrs == normalized
            and row["variant_manufacturer_part_number"]
            == normalized.get("manufacturer_part_number")
            and row["variant_base_unit"] == row["base_unit"] == "PIECE"
            and row["variant_quantity_value"] == row["base_quantity_value"] == 1
            and row["variant_quantity_min"] is None
            and row["variant_quantity_max"] is None
            and row["variant_measure_type"] == row["revision_measure_type"] == "COUNT"
            and row["variant_condition_code"] == row["revision_condition_code"] == "NEW"
            and row["variant_package_count"] == row["revision_package_count"] == 1
            and row["crawl_entity_type"] == "PRODUCT"
            and row["crawl_entity_key"] == row["official_product_id"]
        ):
            raise ValueError("device catalog projection violates the frozen profile")
        identity: dict[str, Any] = {
            "kind": "device",
            **{key: row[key] for key in (
                "category_code", "brand_code", "brand_name", "catalog_item_id",
                "item_variant_id", "product_name", "series_name", "model_number",
                "official_product_id", "official_sku_id",
            )},
            "specification": normalized,
            "match": {
                "listing_revision_id": row["match_listing_revision_id"],
                "item_variant_id": row["match_item_variant_id"],
                "status": row["match_status"],
                "effective_from": _utc(row["match_effective_from"]),
                "effective_to": (
                    _utc(row["match_effective_to"])
                    if row["match_effective_to"] is not None else None
                ),
            },
        }
    elif kind == "fresh":
        source_day = _utc(row["observation_observed_at"]).astimezone(ZoneInfo("Asia/Shanghai")).date()
        if channel == "SH_FGW_FRESH_RETAIL":
            dataset_key = f"shanghai-fresh-retail:{source_day.isoformat()}"
        elif channel == "MOFCOM_FRESH_WHOLESALE":
            # This is the publisher's own commodity ID, not our normalized
            # commodity_code and not a value inferred from a question or name.
            native_id = attrs.get("source_commodity_id")
            if not isinstance(native_id, str) or re.fullmatch(r"[1-9][0-9]{0,31}", native_id) is None:
                raise ValueError("missing proven publisher commodity ID")
            dataset_key = f"mofcom-bj:{native_id}:{source_day.isoformat()}"
        else:
            raise ValueError("unsupported fresh publisher")
        expected_entity_key = "PUBLIC_PRICE:" + sha256(dataset_key.encode()).hexdigest()
        if not (
            row["normalizer_version"] == "government-fresh@1"
            and row["revision_measure_type"] == "WEIGHT"
            and row["crawl_entity_type"] == "PUBLIC_PRICE"
            and row["crawl_entity_key"] == expected_entity_key
        ):
            raise ValueError("fresh projection violates the frozen source profile")
        identity = {
            "kind": "fresh", "category_code": "FRESH_MONITORED_COMMODITY",
            **{key: normalized[key] for key in (
                "commodity_code", "commodity_name", "commodity_group",
                "source_specification", "quoted_unit",
            )},
            "source_market_id": attrs.get("source_market_id"),
            "source_market_name": attrs.get("source_market_name"),
        }
    else:
        raise ValueError("unsupported read kind")

    availability_only = row["price_type"] == "AVAILABILITY_ONLY"
    observation = {
        "kind": "availability_only" if availability_only else "priced",
        **{key: row[key] for key in (
            "observation_id", "crawl_record_id", "currency", "price_nature",
            "price_type", "pricing_basis", "availability", "current_price",
            "original_price", "original_price_type", "fee_status",
        )},
        "source_listing_id": row["observation_source_listing_id"],
        "listing_revision_id": row["observation_listing_revision_id"],
        "region": {"scope": row["observation_region_scope"], "code": row["observation_region_code"]},
        "observed_at": _utc(row["observation_observed_at"]),
        "quality_status": row["observation_quality_status"],
        "time_precision": attrs.get("time_precision", "UNKNOWN"),
        "unit_price": (
            {"amount": row["unit_price"], "unit": row["unit_price_unit"]}
            if row["unit_price"] is not None else None
        ),
    }
    if availability_only:
        observation["promotion_label"] = row["promotion_label"]
    source = {
        key: row[key] for key in (
            "source_channel_id", "merchant_source_channel_id", "channel_code",
            "channel_name", "source_type", "business_mode", "seller_type",
            "verification_status",
        )
    }
    source["source_url"] = source_url
    return ProductPriceReadRecord.model_validate({
        "read_at": read_at,
        "listing": {
            **{key: row[key] for key in (
                "source_listing_id", "listing_revision_id", "revision_source_listing_id",
                "current_revision_id", "revision_quality_status", "base_quantity_value", "base_unit",
            )},
            "price_nature": row["listing_price_nature"],
            "source": source,
            "identity": identity,
        },
        "pointer": {
            "source_listing_id": row["pointer_source_listing_id"],
            "listing_revision_id": row["pointer_listing_revision_id"],
            "price_observation_id": row["pointer_observation_id"],
            "region": {"scope": row["pointer_region_scope"], "code": row["pointer_region_code"]},
            "observed_at": _utc(row["pointer_observed_at"]),
        },
        "observation": observation,
        # Cross-revision equivalence and same-time correction selection are not
        # inferred here. The optional historical supplement stays absent in B.
        "last_known_price": None,
    })


def to_product_price_record(
    row: Mapping[str, Any], *, kind: str, read_at: datetime,
) -> ProductPriceReadRecord:
    try:
        return _record(row, kind=kind, read_at=read_at)
    except (ValueError, TypeError, KeyError, ValidationError, OverflowError):
        # ValidationError/driver rows may carry URLs, IDs and data. They must not
        # become public error details or exception chains.
        raise ProductPriceContractError("价格数据不符合消费合同") from None
