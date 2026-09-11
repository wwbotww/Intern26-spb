from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from spb_assistant_api.domain.exceptions import (
    PriceRepositoryUnavailableError,
    ProductPriceContractError,
    ProductPriceTimeoutError,
)
from spb_assistant_api.domain.product_price import ProductPriceReadRecord
from spb_assistant_api.domain.product_price_query import (
    PRODUCT_PRICE_READ_QUERY,
    DevicePriceReadQuery,
    FreshPriceReadQuery,
    PriceFreshness,
    PriceReadBatch,
    ProductPriceCandidateFact,
    ProductPriceQueryResult,
)
from spb_assistant_api.services.product_price_query import ProductPriceQueryService


_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/product_price/read_records.json").read_text(
        encoding="utf-8"
    )
)


def _raw(name: str = "device_priced") -> dict[str, Any]:
    return deepcopy(_FIXTURES[name])


def _record(name: str = "device_priced", *, offset: int = 0) -> ProductPriceReadRecord:
    raw = _raw(name)
    if offset:
        raw["listing"]["source_listing_id"] += offset
        raw["listing"]["revision_source_listing_id"] += offset
        for key in ("pointer", "observation"):
            raw[key]["source_listing_id"] += offset
        raw["pointer"]["price_observation_id"] += offset
        raw["observation"]["observation_id"] += offset
        if raw["last_known_price"] is not None:
            raw["last_known_price"]["source_listing_id"] += offset
            raw["last_known_price"]["observation_id"] += offset
    return ProductPriceReadRecord.model_validate(raw)


class _Repository:
    def __init__(self, *records: ProductPriceReadRecord, truncated: bool = False,
                 error: Exception | None = None) -> None:
        self.batch = PriceReadBatch(records=records, truncated=truncated)
        self.error = error
        self.queries: list[Any] = []

    async def initialize(self) -> None:
        raise AssertionError("borrowed repository must not be initialized by the service")

    async def close(self) -> None:
        raise AssertionError("borrowed repository must not be closed by the service")

    def readiness(self) -> str:
        raise AssertionError("search must not manufacture a readiness-dependent no-match")

    async def search(self, query: Any) -> PriceReadBatch:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.batch


@pytest.mark.parametrize("kind", ["device", "fresh"])
def test_query_normalizes_search_terms_without_changing_literal_metacharacters(kind: str) -> None:
    query = PRODUCT_PRICE_READ_QUERY.validate_python({
        "kind": kind, "terms": [" Alpha ", "ALPHA", "合成商品", " 100%_! "],
    })
    assert query.terms == ("alpha", "合成商品", "100%_!")
    assert PRODUCT_PRICE_READ_QUERY.validate_json(query.model_dump_json()) == query
    with pytest.raises(ValidationError):
        query.terms = ("changed",)


@pytest.mark.parametrize("kind", ["device", "fresh"])
@pytest.mark.parametrize("terms", [[], [""], [" \t "], [1], [True], [None],
                                   ["x" * 101], ["x"] * 9, "alpha"])
def test_queries_reject_unbounded_or_nontext_terms(kind: str, terms: Any) -> None:
    with pytest.raises(ValidationError):
        PRODUCT_PRICE_READ_QUERY.validate_python({"kind": kind, "terms": terms})


@pytest.mark.parametrize(("kind", "field", "upper"), [
    ("device", "product_limit", 20), ("device", "per_product_limit", 50),
    ("fresh", "listing_limit", 100),
])
@pytest.mark.parametrize("value", [0, -1, True, False, "1", 1.0, None, "over"])
def test_candidate_budgets_are_strict_positive_bounded_integers(
    kind: str, field: str, upper: int, value: Any,
) -> None:
    with pytest.raises(ValidationError):
        PRODUCT_PRICE_READ_QUERY.validate_python({
            "kind": kind, "terms": ["x"], field: upper + 1 if value == "over" else value,
        })


@pytest.mark.parametrize("value", [
    {"kind": "other", "terms": ["x"]},
    {"terms": ["x"]},
    {"kind": "device", "terms": ["x"], "brand_code": "apple"},
    {"kind": "device", "terms": ["x"], "category_code": "FOOD"},
    {"kind": "device", "terms": ["x"], "listing_limit": 5},
    {"kind": "fresh", "terms": ["x"], "product_limit": 5},
    {"kind": "fresh", "terms": ["x"], "price_nature": "RETAIL_OFFER"},
    {"kind": "fresh", "terms": ["x"], "commodity_code": "lowercase"},
    {"kind": "fresh", "terms": ["x"], "market_id": " "},
    {"kind": "fresh", "terms": ["x"], "region": {"scope": "CITY", "code": "CN"}},
    {"kind": "fresh", "terms": ["x"], "sql": "SELECT *"},
])
def test_read_query_union_rejects_unknown_capabilities_and_fields(value: Any) -> None:
    with pytest.raises(ValidationError):
        PRODUCT_PRICE_READ_QUERY.validate_python(value)


def test_budget_upper_bounds_and_maximum_terms_are_accepted() -> None:
    terms = tuple(str(n) for n in range(8))
    assert DevicePriceReadQuery(terms=terms, product_limit=20, per_product_limit=50).terms == terms
    assert FreshPriceReadQuery(terms=("x" * 100,), listing_limit=100).listing_limit == 100


@pytest.mark.parametrize("duplicate_kind", ["same_fact", "same_listing", "same_observation"])
def test_batch_rejects_duplicate_pointer_or_observation_joins(duplicate_kind: str) -> None:
    first = _record()
    raw = _record(offset=1000).model_dump(mode="json")
    if duplicate_kind == "same_fact":
        second = first
    else:
        if duplicate_kind == "same_listing":
            raw["listing"]["source_listing_id"] = first.listing.source_listing_id
            raw["listing"]["revision_source_listing_id"] = first.listing.source_listing_id
            for key in ("pointer", "observation"):
                raw[key]["source_listing_id"] = first.listing.source_listing_id
        else:
            raw["pointer"]["price_observation_id"] = first.observation.observation_id
            raw["observation"]["observation_id"] = first.observation.observation_id
        second = ProductPriceReadRecord.model_validate(raw)
    with pytest.raises(ValidationError, match="duplicate"):
        PriceReadBatch(records=(first, second))


def test_batch_allows_distinct_regions_for_one_fresh_listing() -> None:
    first = _record("fresh_wholesale_kg")
    raw = first.model_dump(mode="json")
    raw["pointer"]["price_observation_id"] += 1000
    raw["observation"]["observation_id"] += 1000
    for key in ("pointer", "observation"):
        raw[key]["region"] = {"scope": "PROVINCE", "code": "310000"}
    second = ProductPriceReadRecord.model_validate(raw)
    assert len(PriceReadBatch(records=(first, second)).records) == 2


@pytest.mark.parametrize("truncated", [True, 1, "true"])
def test_empty_batch_cannot_claim_truncation_or_coerce_boolean(truncated: Any) -> None:
    with pytest.raises(ValidationError):
        PriceReadBatch(truncated=truncated)


def test_batch_rejects_more_than_absolute_record_budget() -> None:
    with pytest.raises(ValidationError):
        PriceReadBatch(records=(_record(),) * 1001)


@pytest.mark.parametrize(("name", "query"), [
    ("device_priced", DevicePriceReadQuery(terms=("alpha",))),
    ("fresh_retail_500g", FreshPriceReadQuery(terms=("黄瓜",))),
    ("fresh_wholesale_kg", FreshPriceReadQuery(
        terms=("鸡蛋",), commodity_code="SYNTHETIC_EGG", market_id="synthetic-market",
        region={"scope": "PROVINCE", "code": "XJ_CORPS"}, price_nature="WHOLESALE_AVERAGE",
    )),
    ("device_availability", DevicePriceReadQuery(terms=("watch",), category_code="WATCH")),
])
def test_service_returns_exact_candidate_facts_with_unknown_default_freshness(name: str, query: Any) -> None:
    record = _record(name)
    repository = _Repository(record, truncated=True)
    result = asyncio.run(ProductPriceQueryService(repository).search(query))
    assert result.status == "candidates"
    assert result.truncated is True
    assert result.candidates[0].record == record
    assert result.candidates[0].freshness == PriceFreshness(
        status="unknown", observed_at=record.observation.observed_at, evaluated_at=record.read_at,
    )
    assert repository.queries == [query]


@pytest.mark.parametrize(("name", "kwargs"), [
    ("fresh_retail_500g", {"kind": "device"}),
    ("device_priced", {"kind": "fresh"}),
    ("device_priced", {"kind": "device", "brand_code": "HUAWEI"}),
    ("device_priced", {"kind": "device", "category_code": "WATCH"}),
    ("fresh_wholesale_kg", {"kind": "fresh", "commodity_code": "OTHER"}),
    ("fresh_wholesale_kg", {"kind": "fresh", "market_id": "another-market"}),
    ("fresh_retail_500g", {"kind": "fresh", "market_id": "synthetic-market"}),
    ("fresh_wholesale_kg", {"kind": "fresh", "price_nature": "RETAIL_AVERAGE"}),
    ("fresh_wholesale_kg", {"kind": "fresh", "region": {"scope": "CITY", "code": "310100"}}),
])
def test_service_rejects_repository_facts_outside_requested_scope(name: str, kwargs: dict[str, Any]) -> None:
    query = PRODUCT_PRICE_READ_QUERY.validate_python({"terms": ["x"], **kwargs})
    with pytest.raises(ProductPriceContractError):
        asyncio.run(ProductPriceQueryService(_Repository(_record(name))).search(query))


@pytest.mark.parametrize("budget", ["products", "variants", "fresh"])
def test_service_enforces_repository_candidate_budgets(budget: str) -> None:
    if budget == "products":
        records = (_record(), _record("device_availability"))
        query = DevicePriceReadQuery(terms=("alpha",), product_limit=1)
    elif budget == "variants":
        records = (_record(), _record(offset=1000))
        query = DevicePriceReadQuery(terms=("alpha",), per_product_limit=1)
    else:
        records = (_record("fresh_retail_500g"), _record("fresh_wholesale_kg"))
        query = FreshPriceReadQuery(terms=("合成",), listing_limit=1)
    with pytest.raises(ProductPriceContractError, match="预算"):
        asyncio.run(ProductPriceQueryService(_Repository(*records)).search(query))


def test_service_validates_before_repository_and_does_not_own_lifecycle() -> None:
    repository = _Repository()
    service = ProductPriceQueryService(repository)
    with pytest.raises(ValidationError):
        asyncio.run(service.search({"kind": "device", "terms": []}))
    assert repository.queries == []
    for _ in range(2):
        result = asyncio.run(service.search({"kind": "device", "terms": [" ALPHA "]}))
        assert result == ProductPriceQueryResult(status="no_match")
    assert repository.queries == [DevicePriceReadQuery(terms=("alpha",))] * 2


@pytest.mark.parametrize("error_type", [
    PriceRepositoryUnavailableError, ProductPriceContractError, ProductPriceTimeoutError,
    RuntimeError, TimeoutError,
])
def test_repository_failure_is_propagated_instead_of_becoming_no_match(error_type: type[Exception]) -> None:
    error = error_type("synthetic failure")
    service = ProductPriceQueryService(_Repository(error=error))
    with pytest.raises(error_type) as caught:
        asyncio.run(service.search(DevicePriceReadQuery(terms=("alpha",))))
    assert caught.value is error


def test_cancellation_propagates_without_closing_borrowed_repository() -> None:
    class CancelledRepository(_Repository):
        async def search(self, query: Any) -> PriceReadBatch:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ProductPriceQueryService(CancelledRepository()).search(
            DevicePriceReadQuery(terms=("alpha",)),
        ))


@pytest.mark.parametrize(("ttl", "expected"), [(3599, "stale"), (3600, "fresh"), (3601, "fresh")])
def test_source_freshness_uses_read_time_and_inclusive_threshold(ttl: int, expected: str) -> None:
    record = _record()
    thresholds = {"APPLE_CN_WEB": ttl}
    service = ProductPriceQueryService(_Repository(record), freshness_max_age_seconds=thresholds)
    thresholds["APPLE_CN_WEB"] = 1  # construction owns a copy of the caller's policy
    result = asyncio.run(service.search(DevicePriceReadQuery(terms=("alpha",))))
    freshness = result.candidates[0].freshness
    assert freshness.status == expected
    assert freshness.max_age_seconds == ttl
    assert freshness.evaluated_at == record.read_at
    assert freshness.observed_at == record.observation.observed_at


def test_freshness_is_source_specific_not_a_global_price_ttl() -> None:
    records = (_record("fresh_retail_500g"), _record("fresh_wholesale_kg"))
    service = ProductPriceQueryService(
        _Repository(*records), freshness_max_age_seconds={"SH_FGW_FRESH_RETAIL": 3600},
    )
    result = asyncio.run(service.search(FreshPriceReadQuery(terms=("合成",))))
    assert [fact.freshness.status for fact in result.candidates] == ["stale", "unknown"]
    assert [fact.freshness.max_age_seconds for fact in result.candidates] == [3600, None]


def test_amountless_current_state_keeps_history_separate_and_ages_current_observation() -> None:
    record = _record("device_availability")
    service = ProductPriceQueryService(_Repository(record), freshness_max_age_seconds={"APPLE_CN_WEB": 3600})
    fact = asyncio.run(service.search(DevicePriceReadQuery(terms=("watch",)))).candidates[0]
    assert fact.record.observation.kind == "availability_only"
    assert fact.record.observation.current_price is None
    assert fact.record.last_known_price == record.last_known_price
    assert fact.record.last_known_price.observed_at < fact.freshness.observed_at
    assert fact.freshness.status == "fresh"


@pytest.mark.parametrize("policy", [{"": 1}, {" ": 1}, {1: 1}, {"APPLE_CN_WEB": True},
                                   {"APPLE_CN_WEB": 1.0}, {"APPLE_CN_WEB": "1"},
                                   {"APPLE_CN_WEB": 0}, {"APPLE_CN_WEB": -1},
                                   {"APPLE_CN_WEB": 366 * 86400 + 1}])
def test_freshness_policy_rejects_implicit_or_unbounded_thresholds(policy: Any) -> None:
    with pytest.raises(ValueError):
        ProductPriceQueryService(_Repository(), freshness_max_age_seconds=policy)


@pytest.mark.parametrize(("status", "ttl", "age"), [
    ("fresh", None, 0), ("stale", None, 10), ("unknown", 10, 0),
    ("fresh", 10, 11), ("stale", 10, 10), ("unknown", None, -1),
    ("fresh", True, 0), ("fresh", 0, 0), ("fresh", 366 * 86400 + 1, 0),
])
def test_freshness_fact_cannot_contradict_its_time_and_policy(status: str, ttl: Any, age: int) -> None:
    observed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        PriceFreshness(status=status, observed_at=observed,
                       evaluated_at=observed + timedelta(seconds=age), max_age_seconds=ttl)


@pytest.mark.parametrize("field", ["observed_at", "evaluated_at"])
def test_freshness_rejects_naive_timestamps(field: str) -> None:
    values = {
        "status": "unknown",
        "observed_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "evaluated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    values[field] = values[field].replace(tzinfo=None)
    with pytest.raises(ValidationError):
        PriceFreshness(**values)


@pytest.mark.parametrize("field", ["observed_at", "evaluated_at"])
def test_candidate_freshness_must_describe_its_own_current_record(field: str) -> None:
    record = _record()
    freshness = {"status": "unknown", "observed_at": record.observation.observed_at,
                 "evaluated_at": record.read_at}
    freshness[field] += timedelta(seconds=1)
    with pytest.raises(ValidationError, match="current observation"):
        ProductPriceCandidateFact(record=record, freshness=PriceFreshness(**freshness))


@pytest.mark.parametrize(("status", "has_candidate", "truncated"), [
    ("candidates", False, False), ("no_match", True, False), ("no_match", False, True),
    ("no_match", False, 1),
])
def test_query_result_status_and_truncation_cannot_misrepresent_candidates(
    status: str, has_candidate: bool, truncated: Any,
) -> None:
    record = _record()
    fact = ProductPriceCandidateFact(record=record, freshness=PriceFreshness(
        status="unknown", observed_at=record.observation.observed_at, evaluated_at=record.read_at,
    ))
    with pytest.raises(ValidationError):
        ProductPriceQueryResult(status=status, candidates=(fact,) if has_candidate else (), truncated=truncated)
