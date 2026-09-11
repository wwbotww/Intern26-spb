from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from spb_assistant_api.domain.product_price import (
    AvailabilityObservation,
    FreshPriceIdentity,
    PricedObservation,
    ProductPriceReadRecord,
)


_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/product_price/read_records.json").read_text(
        encoding="utf-8"
    )
)


def _record(name: str = "device_priced") -> dict[str, Any]:
    return deepcopy(_FIXTURES[name])


def _set(record: dict[str, Any], dotted_path: str, value: Any) -> None:
    *parents, key = dotted_path.split(".")
    container = record
    for parent in parents:
        container = container[parent]
    container[key] = value


def _reject(name: str, dotted_path: str, value: Any) -> None:
    record = _record(name)
    _set(record, dotted_path, value)
    with pytest.raises(ValidationError):
        ProductPriceReadRecord.model_validate(record)


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_synthetic_contract_records_validate_and_round_trip(name: str) -> None:
    record = ProductPriceReadRecord.model_validate(_record(name))

    assert ProductPriceReadRecord.model_validate_json(record.model_dump_json()) == record
    assert record.pointer.price_observation_id == record.observation.observation_id
    assert record.pointer.observed_at <= record.read_at


@pytest.mark.parametrize(
    "dotted_path",
    [
        "observation.current_price",
        "observation.original_price",
        "observation.unit_price.amount",
        "listing.base_quantity_value",
    ],
)
@pytest.mark.parametrize(
    "value",
    [
        0, "0", "-1", 1.0, True, False, float("nan"), float("inf"),
        "NaN", "Infinity", Decimal("NaN"), Decimal("Infinity"),
    ],
)
def test_price_and_quantity_require_positive_exact_finite_decimals(
    dotted_path: str, value: Any
) -> None:
    _reject("device_priced", dotted_path, value)


@pytest.mark.parametrize(
    ("dotted_path", "value"),
    [
        ("observation.current_price", "1200.001"),
        ("observation.original_price", "1500.001"),
        ("observation.unit_price.amount", "1200.0000001"),
        ("listing.base_quantity_value", "1.0000001"),
        ("observation.current_price", "10000000000000000.00"),
        ("observation.unit_price.amount", "100000000000000.000000"),
    ],
)
def test_decimal_scale_and_precision_are_bounded(dotted_path: str, value: str) -> None:
    _reject("device_priced", dotted_path, value)


def test_large_decimal_survives_json_without_binary_float_rounding() -> None:
    raw = _record()
    raw["observation"]["current_price"] = "9999999999999999.99"
    raw["observation"]["original_price"] = None
    raw["observation"]["original_price_type"] = "NONE"
    raw["observation"]["unit_price"] = None

    record = ProductPriceReadRecord.model_validate(raw)
    encoded = json.loads(record.model_dump_json())

    assert record.observation.current_price == Decimal("9999999999999999.99")
    assert encoded["observation"]["current_price"] == "9999999999999999.99"
    assert ProductPriceReadRecord.model_validate(encoded) == record


def test_absent_original_price_is_not_inferred_from_current_price() -> None:
    raw = _record()
    raw["observation"]["original_price"] = None
    raw["observation"]["original_price_type"] = "NONE"

    record = ProductPriceReadRecord.model_validate(raw)

    assert record.observation.current_price == Decimal("1200.00")
    assert record.observation.original_price is None
    assert record.observation.original_price_type == "NONE"


def test_exact_integer_inputs_do_not_require_binary_float_conversion() -> None:
    raw = _record()
    raw["observation"]["current_price"] = 1200
    raw["observation"]["unit_price"]["amount"] = Decimal("1200")
    raw["listing"]["base_quantity_value"] = 1

    record = ProductPriceReadRecord.model_validate(raw)

    assert record.observation.current_price == Decimal("1200")
    assert record.observation.unit_price.amount == Decimal("1200")


@pytest.mark.parametrize(
    ("dotted_path", "value"),
    [("observation.original_price", None), ("observation.original_price_type", "NONE")],
)
def test_original_price_and_its_type_must_agree(dotted_path: str, value: Any) -> None:
    _reject("device_priced", dotted_path, value)


@pytest.mark.parametrize(
    ("name", "dotted_path", "amount"),
    [
        ("device_priced", "observation.original_price", "1199.99"),
        ("device_availability", "last_known_price.original_price", "499.99"),
    ],
)
def test_official_device_original_price_cannot_be_below_current_price(
    name: str, dotted_path: str, amount: str
) -> None:
    raw = _record(name)
    observation_key = dotted_path.split(".")[0]
    raw[observation_key]["original_price_type"] = "CROSSED_OUT"
    _set(raw, dotted_path, amount)

    with pytest.raises(ValidationError, match="original price cannot be below"):
        ProductPriceReadRecord.model_validate(raw)


@pytest.mark.parametrize(
    ("name", "observation_key"),
    [("device_priced", "observation"), ("device_availability", "last_known_price")],
)
def test_official_device_original_price_may_equal_current_price(
    name: str, observation_key: str
) -> None:
    raw = _record(name)
    raw[observation_key]["original_price"] = raw[observation_key]["current_price"]
    raw[observation_key]["original_price_type"] = "CROSSED_OUT"

    record = ProductPriceReadRecord.model_validate(raw)
    observation = getattr(record, observation_key)

    assert observation.original_price == observation.current_price


def test_retail_original_price_rule_does_not_leak_into_generic_wholesale_facts() -> None:
    raw = _record()["observation"]
    raw["price_nature"] = "WHOLESALE_OFFER"
    raw["original_price"] = "1199.99"

    observation = PricedObservation.model_validate(raw)

    assert observation.original_price < observation.current_price
    assert observation.price_nature == "WHOLESALE_OFFER"


@pytest.mark.parametrize("name", ["fresh_retail_500g", "fresh_wholesale_kg"])
@pytest.mark.parametrize("original_price_type", ["CROSSED_OUT", "MSRP", "EXPLICIT_ORIGINAL"])
def test_government_average_profiles_cannot_invent_a_marketing_original_price(
    name: str, original_price_type: str
) -> None:
    raw = _record(name)
    raw["observation"]["original_price"] = "10.00"
    raw["observation"]["original_price_type"] = original_price_type

    with pytest.raises(ValidationError):
        ProductPriceReadRecord.model_validate(raw)


@pytest.mark.parametrize(
    ("name", "amount", "normalized", "quantity"),
    [
        ("fresh_retail_500g", "3.50", "7.000000", "0.500"),
        ("fresh_wholesale_kg", "8.20", "8.200000", "1"),
    ],
)
def test_original_quote_and_normalized_kg_amount_remain_distinct(
    name: str, amount: str, normalized: str, quantity: str
) -> None:
    record = ProductPriceReadRecord.model_validate(_record(name))

    assert record.observation.current_price == Decimal(amount)
    assert record.observation.unit_price.amount == Decimal(normalized)
    assert record.listing.base_quantity_value == Decimal(quantity)
    assert record.observation.unit_price.unit == "CNY_PER_KG"


@pytest.mark.parametrize(
    ("name", "dotted_path", "value"),
    [
        ("fresh_retail_500g", "observation.unit_price.amount", "3.50"),
        ("fresh_retail_500g", "observation.unit_price.unit", "CNY_PER_PIECE"),
        ("fresh_retail_500g", "listing.base_quantity_value", "1"),
        ("fresh_retail_500g", "listing.identity.quoted_unit", "CNY_PER_KG"),
        ("fresh_wholesale_kg", "observation.unit_price.amount", "16.40"),
        ("fresh_wholesale_kg", "observation.unit_price", None),
        ("fresh_wholesale_kg", "listing.base_unit", "PIECE"),
        ("device_priced", "observation.unit_price.unit", "CNY_PER_KG"),
        ("device_priced", "listing.base_quantity_value", "2"),
    ],
)
def test_incompatible_unit_or_conversion_is_not_silently_repaired(
    name: str, dotted_path: str, value: Any
) -> None:
    _reject(name, dotted_path, value)


@pytest.mark.parametrize("availability", ["OFF_SHELF", "OUT_OF_STOCK", "COMING_SOON"])
def test_amountless_status_keeps_last_known_price_separate(availability: str) -> None:
    raw = _record("device_availability")
    raw["observation"]["availability"] = availability

    record = ProductPriceReadRecord.model_validate(raw)

    assert isinstance(record.observation, AvailabilityObservation)
    assert record.observation.current_price is None
    assert record.observation.unit_price is None
    assert record.last_known_price.current_price == Decimal("500.00")
    assert record.last_known_price.observed_at < record.observation.observed_at


def test_amountless_fact_does_not_require_a_historical_amount() -> None:
    raw = _record("device_availability")
    raw["last_known_price"] = None

    record = ProductPriceReadRecord.model_validate(raw)

    assert record.observation.current_price is None
    assert record.last_known_price is None


@pytest.mark.parametrize("availability", ["OFF_SHELF", "OUT_OF_STOCK", "UNKNOWN"])
def test_explicit_priced_fact_is_not_rewritten_based_on_availability(
    availability: str,
) -> None:
    raw = _record()
    raw["observation"]["availability"] = availability

    record = ProductPriceReadRecord.model_validate(raw)

    assert record.observation.current_price == Decimal("1200.00")
    assert record.observation.availability == availability
    assert record.last_known_price is None


@pytest.mark.parametrize(
    ("dotted_path", "value"),
    [
        ("observation.current_price", "1.00"),
        ("observation.original_price", "1.00"),
        ("observation.original_price_type", "MSRP"),
        ("observation.unit_price", {"amount": "1", "unit": "CNY_PER_PIECE"}),
        ("observation.promotion_label", "低至合成金额"),
        ("observation.availability", "ON_SALE"),
        ("observation.availability", "UNKNOWN"),
        ("observation.availability", "RESERVATION"),
        ("observation.availability", "PRE_SALE"),
        ("observation.price_nature", "WHOLESALE_OFFER"),
        ("observation.price_type", "DIRECT_UNCONDITIONAL"),
        ("observation.pricing_basis", "PACKAGE_TOTAL"),
        ("observation.fee_status", "ITEM_ONLY"),
    ],
)
def test_amountless_fact_rejects_money_promotions_and_wrong_semantics(
    dotted_path: str, value: Any
) -> None:
    _reject("device_availability", dotted_path, value)


@pytest.mark.parametrize(
    ("dotted_path", "value"),
    [
        ("listing.revision_source_listing_id", 999),
        ("listing.current_revision_id", 999),
        ("pointer.source_listing_id", 999),
        ("pointer.listing_revision_id", 999),
        ("pointer.price_observation_id", 999),
        ("pointer.region", {"scope": "CITY", "code": "310100"}),
        ("pointer.observed_at", "2026-01-16T03:01:00Z"),
        ("observation.source_listing_id", 999),
        ("observation.listing_revision_id", 999),
        ("observation.quality_status", "NEEDS_REVIEW"),
        ("listing.revision_quality_status", "REJECTED"),
        ("observation.price_nature", "WHOLESALE_OFFER"),
    ],
)
def test_incoherent_pointer_revision_or_quality_fails_closed(
    dotted_path: str, value: Any
) -> None:
    _reject("device_priced", dotted_path, value)


@pytest.mark.parametrize(
    ("dotted_path", "value"),
    [
        ("listing.identity.match.listing_revision_id", 999),
        ("listing.identity.match.item_variant_id", 999),
        ("listing.identity.match.status", "PROPOSED"),
        ("listing.identity.match.status", "REJECTED"),
        ("listing.identity.match.effective_from", "2026-01-16T04:00:01Z"),
        ("listing.identity.match.effective_to", "2026-01-16T04:00:00Z"),
        ("listing.identity.match.effective_to", "2026-01-15T03:00:00Z"),
    ],
)
def test_device_match_requires_accepted_identity_and_current_validity(
    dotted_path: str, value: Any
) -> None:
    _reject("device_priced", dotted_path, value)


def test_match_validity_is_start_inclusive_end_exclusive() -> None:
    raw = _record()
    raw["listing"]["identity"]["match"]["effective_from"] = raw["read_at"]
    raw["listing"]["identity"]["match"]["effective_to"] = "2026-01-16T04:00:01Z"

    assert ProductPriceReadRecord.model_validate(raw).listing.identity.match is not None


@pytest.mark.parametrize(
    ("dotted_path", "value"),
    [
        ("last_known_price.source_listing_id", 999),
        ("last_known_price.listing_revision_id", 999),
        ("last_known_price.region", {"scope": "CITY", "code": "310100"}),
        ("last_known_price.observation_id", 504),
        ("last_known_price.observed_at", "2026-01-16T03:00:00Z"),
        ("last_known_price.observed_at", "2026-01-16T03:00:01Z"),
        ("last_known_price.price_nature", "WHOLESALE_OFFER"),
    ],
)
def test_last_known_price_cannot_cross_identity_or_impersonate_current(
    dotted_path: str, value: Any
) -> None:
    _reject("device_availability", dotted_path, value)


def test_priced_current_record_cannot_attach_a_last_known_price() -> None:
    raw = _record()
    raw["last_known_price"] = deepcopy(raw["observation"])
    raw["last_known_price"]["observation_id"] = 499
    raw["last_known_price"]["observed_at"] = "2026-01-15T03:00:00Z"

    with pytest.raises(ValidationError, match="only a supplement"):
        ProductPriceReadRecord.model_validate(raw)


@pytest.mark.parametrize("brand", ["APPLE", "HUAWEI", "XIAOMI", "OPPO", "VIVO"])
def test_supported_official_brand_and_source_pair_is_consistent(brand: str) -> None:
    raw = _record()
    raw["listing"]["identity"]["brand_code"] = brand
    raw["listing"]["identity"]["brand_name"] = brand
    raw["listing"]["source"]["channel_code"] = f"{brand}_CN_WEB"

    assert ProductPriceReadRecord.model_validate(raw).listing.identity.brand_code == brand


@pytest.mark.parametrize(
    ("name", "dotted_path", "value"),
    [
        ("device_priced", "listing.source.channel_code", "HUAWEI_CN_WEB"),
        ("device_priced", "listing.source.source_type", "PUBLIC_DATA"),
        ("device_priced", "listing.source.seller_type", "UNKNOWN"),
        ("device_priced", "listing.source.verification_status", "UNVERIFIED"),
        ("device_priced", "listing.source.business_mode", "WHOLESALE"),
        ("device_priced", "listing.source.merchant_source_channel_id", 999),
        ("fresh_retail_500g", "listing.source.seller_type", "BRAND_OFFICIAL"),
        ("fresh_wholesale_kg", "listing.source.verification_status", "UNKNOWN"),
        ("fresh_wholesale_kg", "listing.source.merchant_source_channel_id", 999),
        ("fresh_retail_500g", "listing.source.channel_code", "UNREVIEWED_SOURCE"),
        ("fresh_retail_500g", "observation.availability", "ON_SALE"),
        ("fresh_retail_500g", "observation.price_type", "DIRECT_UNCONDITIONAL"),
        ("fresh_retail_500g", "observation.fee_status", "ITEM_ONLY"),
    ],
)
def test_source_and_price_profile_must_be_proven_together(
    name: str, dotted_path: str, value: Any
) -> None:
    _reject(name, dotted_path, value)


@pytest.mark.parametrize(
    ("name", "region"),
    [
        ("fresh_wholesale_kg", {"scope": "UNKNOWN", "code": "UNKNOWN"}),
        ("fresh_wholesale_kg", {"scope": "MULTI", "code": "MULTI"}),
        ("fresh_wholesale_kg", {"scope": "PROVINCE", "code": "UNKNOWN"}),
        ("fresh_wholesale_kg", {"scope": "PROVINCE", "code": "MULTI"}),
        ("fresh_wholesale_kg", {"scope": "PROVINCE", "code": "CN"}),
        ("fresh_wholesale_kg", {"scope": "CITY", "code": "310100"}),
        ("fresh_retail_500g", {"scope": "CITY", "code": "110100"}),
        ("fresh_retail_500g", {"scope": "PROVINCE", "code": "310000"}),
        ("device_priced", {"scope": "CITY", "code": "310100"}),
        ("device_priced", {"scope": "NATIONAL", "code": "US"}),
    ],
)
def test_region_cannot_be_unknown_multiregion_or_an_incompatible_source_scope(
    name: str, region: dict[str, str]
) -> None:
    raw = _record(name)
    raw["pointer"]["region"] = region
    raw["observation"]["region"] = region

    with pytest.raises(ValidationError):
        ProductPriceReadRecord.model_validate(raw)


def test_source_province_special_code_is_not_rewritten_as_administrative_digits() -> None:
    record = ProductPriceReadRecord.model_validate(_record("fresh_wholesale_kg"))

    assert record.observation.region.scope == "PROVINCE"
    assert record.observation.region.code == "XJ_CORPS"


@pytest.mark.parametrize(
    "dotted_path",
    [
        "read_at", "pointer.observed_at", "observation.observed_at",
        "listing.identity.match.effective_from", "listing.identity.match.effective_to",
    ],
)
def test_timestamps_require_an_explicit_timezone(dotted_path: str) -> None:
    _reject("device_priced", dotted_path, "2026-01-16T03:00:00")


def test_equivalent_utc_and_shanghai_instants_preserve_publication_day() -> None:
    raw = _record("fresh_retail_500g")
    raw["pointer"]["observed_at"] = "2026-01-16T00:00:00+08:00"

    record = ProductPriceReadRecord.model_validate(raw)

    assert record.pointer.observed_at == record.observation.observed_at
    assert record.observation.observed_at.date() == date(2026, 1, 15)
    source_date = record.observation.observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    assert source_date == date(2026, 1, 16)
    assert record.observation.time_precision == "DAY"


@pytest.mark.parametrize(
    "observed_at",
    ["2026-01-16T00:00:00Z", "2026-01-16T00:00:00.000001+08:00"],
)
def test_day_fact_is_not_a_shifted_or_invented_instant(observed_at: str) -> None:
    raw = _record("fresh_retail_500g")
    raw["pointer"]["observed_at"] = observed_at
    raw["observation"]["observed_at"] = observed_at

    with pytest.raises(ValidationError, match="Shanghai source midnight"):
        ProductPriceReadRecord.model_validate(raw)


def test_read_cannot_include_a_future_observation() -> None:
    raw = _record()
    raw["pointer"]["observed_at"] = "2026-01-16T04:00:01Z"
    raw["observation"]["observed_at"] = "2026-01-16T04:00:01Z"

    with pytest.raises(ValidationError):
        ProductPriceReadRecord.model_validate(raw)


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("userinfo", ["user:synthetic-secret@", "user@", ":synthetic-secret@"])
def test_http_source_urls_cannot_embed_credentials(scheme: str, userinfo: str) -> None:
    _reject(
        "device_priced", "listing.source.source_url",
        f"{scheme}://{userinfo}example.invalid/item",
    )


def test_fresh_identity_needs_no_device_catalog_brand_or_variant() -> None:
    record = ProductPriceReadRecord.model_validate(_record("fresh_retail_500g"))

    assert isinstance(record.listing.identity, FreshPriceIdentity)
    identity = record.listing.identity.model_dump()
    assert not {"catalog_item_id", "item_variant_id", "brand_code", "match"} & identity.keys()
    assert record.listing.identity.source_market_id is None
    assert record.listing.identity.source_market_name is None


@pytest.mark.parametrize(
    ("dotted_path", "value"),
    [
        ("listing.identity.commodity_group", "蔬菜"),
        ("listing.identity.commodity_group", "禽蛋"),
        ("listing.identity.commodity_group", "水果"),
        ("listing.identity.commodity_group", "UNREVIEWED_GROUP"),
        ("listing.identity.category_code", "FRESH_VEGETABLE"),
        ("listing.identity.category_code", "FRESH_EGG"),
        ("listing.identity.category_code", "UNREVIEWED_CATEGORY"),
    ],
)
def test_fresh_profile_requires_the_producer_normalized_category_and_group(
    dotted_path: str, value: str
) -> None:
    _reject("fresh_retail_500g", dotted_path, value)


def test_normalized_fruit_group_is_supported_alongside_vegetables_and_meat_eggs() -> None:
    raw = _record("fresh_retail_500g")
    raw["listing"]["identity"]["commodity_group"] = "FRUIT"
    raw["listing"]["identity"]["commodity_code"] = "SYNTHETIC_FRUIT"
    raw["listing"]["identity"]["commodity_name"] = "合成水果"

    record = ProductPriceReadRecord.model_validate(raw)

    assert record.listing.identity.category_code == "FRESH_MONITORED_COMMODITY"
    assert record.listing.identity.commodity_group == "FRUIT"


@pytest.mark.parametrize("field", ["source_market_id", "source_market_name"])
def test_optional_market_identity_is_an_id_and_name_pair(field: str) -> None:
    _reject("fresh_wholesale_kg", f"listing.identity.{field}", None)


@pytest.mark.parametrize("value", [True, 1.0, "101", 0, -1])
def test_database_identifiers_are_strict_positive_integers(value: Any) -> None:
    _reject("device_priced", "listing.source_listing_id", value)


@pytest.mark.parametrize(
    "dotted_path",
    [
        "unexpected", "listing.unexpected", "pointer.unexpected", "observation.unexpected",
        "listing.identity.unexpected", "listing.source.unexpected",
        "listing.identity.specification.unexpected", "listing.identity.match.unexpected",
    ],
)
def test_unreviewed_extra_fields_are_rejected(dotted_path: str) -> None:
    _reject("device_priced", dotted_path, None)


@pytest.mark.parametrize("field", ["price", "current_price", "availability", "observed_at", "URL"])
def test_mutable_quote_fields_cannot_be_variant_identity(field: str) -> None:
    _reject("device_priced", "listing.identity.specification.attributes", {field: "synthetic"})


def test_device_identity_requires_at_least_one_evidenced_specification() -> None:
    _reject("device_priced", "listing.identity.specification", {})
