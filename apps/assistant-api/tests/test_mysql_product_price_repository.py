from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import OperationalError

from spb_assistant_api.adapters.mysql_product_price import (
    COMMON_PROJECTION,
    DEVICE_PROJECTION,
    DEVICE_SQL,
    FRESH_SQL,
    MySQLProductPriceRepository,
)
from spb_assistant_api.adapters.product_price_rows import to_product_price_record
from spb_assistant_api.domain.exceptions import (
    PriceRepositoryUnavailableError,
    ProductPriceContractError,
    ProductPriceTimeoutError,
)
from spb_assistant_api.domain.product_price import ProductPriceReadRecord, PriceRegion
from spb_assistant_api.domain.product_price_query import DevicePriceReadQuery, FreshPriceReadQuery


def _row(case: str = "device_priced") -> dict[str, Any]:
    fixture = json.loads((Path(__file__).parent / "fixtures/product_price/read_records.json").read_text())
    record = ProductPriceReadRecord.model_validate(fixture[case])
    listing, obs, pointer = record.listing, record.observation, record.pointer
    identity, source = listing.identity, listing.source
    row = {
        **listing.model_dump(exclude={"identity", "source", "price_nature"}),
        **source.model_dump(exclude={"source_url"}),
        **obs.model_dump(exclude={"kind", "region", "observed_at", "quality_status", "unit_price",
                                  "source_listing_id", "listing_revision_id", "time_precision"}),
        "listing_price_nature": listing.price_nature,
        "revision_rejection_code": None,
        "observation_rejection_code": None,
        "base_quantity_min": None, "base_quantity_max": None,
        "revision_condition_code": "NEW" if identity.kind == "device" else "UNKNOWN",
        "revision_measure_type": "COUNT" if identity.kind == "device" else "WEIGHT",
        "revision_package_count": 1,
        "source_currency": "CNY",
        "merchant_id": 51, "listing_merchant_id": 51,
        "source_attributes": {"time_precision": obs.time_precision},
        "normalizer_version": "electronic-device@1" if identity.kind == "device" else "government-fresh@1",
        "pointer_source_listing_id": pointer.source_listing_id,
        "pointer_listing_revision_id": pointer.listing_revision_id,
        "pointer_observation_id": pointer.price_observation_id,
        "pointer_region_scope": pointer.region.scope,
        "pointer_region_code": pointer.region.code,
        "pointer_observed_at": pointer.observed_at.replace(tzinfo=None),
        "observation_source_listing_id": obs.source_listing_id,
        "observation_listing_revision_id": obs.listing_revision_id,
        "observation_region_scope": obs.region.scope,
        "observation_region_code": obs.region.code,
        "observation_observed_at": obs.observed_at.replace(tzinfo=None),
        "observation_quality_status": obs.quality_status,
        "unit_price": obs.unit_price.amount if obs.unit_price else None,
        "unit_price_unit": obs.unit_price.unit if obs.unit_price else None,
        "promotion_label": None,
        "crawl_id": obs.crawl_record_id,
        "crawl_source_listing_id": None,
        "record_crawl_run_id": 71, "crawl_run_id": 71,
        "crawl_run_source_channel_id": source.source_channel_id,
        "crawl_entity_type": "PRODUCT" if identity.kind == "device" else "PUBLIC_PRICE",
        "crawl_entity_key": identity.official_product_id if identity.kind == "device" else "PUBLIC_PRICE:synthetic",
        "crawl_fetch_status": "SUCCEEDED", "crawl_parse_status": "SUCCEEDED",
    }
    host = {
        "APPLE_CN_WEB": "www.apple.com.cn",
        "SH_FGW_FRESH_RETAIL": "fgw.sh.gov.cn",
        "MOFCOM_FRESH_WHOLESALE": "cif.mofcom.gov.cn",
    }[source.channel_code]
    row.update({"allowed_domains": json.dumps([host]), "canonical_url": f"https://{host}/synthetic",
                "crawl_request_url": f"https://{host}/synthetic", "crawl_final_url": f"https://{host}/synthetic"})
    if identity.kind == "device":
        spec = identity.specification.model_dump(exclude_none=True)
        row.update(identity.model_dump(exclude={"kind", "specification", "match"}))
        row.update({
            "normalized_attributes": spec, "variant_attributes": copy.deepcopy(spec),
            "variant_manufacturer_part_number": spec.get("manufacturer_part_number"),
            "variant_catalog_item_id": identity.catalog_item_id,
            "item_brand_id": 61, "brand_id": 61,
            "item_category_id": 81, "category_id": 81,
            "item_type": "MODEL", "attribute_profile_code": "electronic-device",
            "attribute_profile_version": "1", "variant_measure_type": "COUNT",
            "variant_condition_code": "NEW", "variant_quantity_value": Decimal(1),
            "variant_quantity_min": None, "variant_quantity_max": None,
            "variant_base_unit": "PIECE", "variant_package_count": 1,
            "match_listing_revision_id": identity.match.listing_revision_id,
            "match_item_variant_id": identity.match.item_variant_id,
            "match_status": identity.match.status, "match_count": 1,
            "match_effective_from": identity.match.effective_from.replace(tzinfo=None),
            "match_effective_to": identity.match.effective_to,
        })
    else:
        row["normalized_attributes"] = identity.model_dump(exclude={"kind", "category_code", "source_market_id", "source_market_name"})
        row["source_attributes"].update(source_market_id=identity.source_market_id, source_market_name=identity.source_market_name)
        source_day = obs.observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        if source.channel_code == "SH_FGW_FRESH_RETAIL":
            dataset_key = f"shanghai-fresh-retail:{source_day}"
        else:
            row["source_attributes"]["source_commodity_id"] = "999"
            dataset_key = f"mofcom-bj:999:{source_day}"
        row["crawl_entity_key"] = "PUBLIC_PRICE:" + sha256(dataset_key.encode()).hexdigest()
    return row


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class FakeConnection:
    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self.batches = list(batches)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.transaction_count = 0

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def begin(self) -> FakeConnection:
        self.transaction_count += 1
        return self

    def execute(self, statement: object, parameters: dict[str, Any]) -> FakeResult:
        self.calls.append((str(statement), parameters))
        return FakeResult(self.batches.pop(0) if self.batches else [])


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.disposed = False

    def connect(self) -> FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def _repository(connection: FakeConnection, *, timeout: float = 1) -> MySQLProductPriceRepository:
    repo = MySQLProductPriceRepository(dsn="mysql+pymysql://unused", pool_size=1,
                                      connect_timeout_seconds=1, query_timeout_seconds=timeout)
    repo._engine = FakeEngine(connection)  # type: ignore[assignment]
    repo._ready = True
    return repo


@pytest.mark.parametrize("case", ["device_priced", "fresh_retail_500g", "fresh_wholesale_kg", "device_availability"])
def test_raw_projection_retains_units_time_and_amountless_state(case: str) -> None:
    row = _row(case)
    kind = "device" if case.startswith("device") else "fresh"
    read_at = datetime.now(timezone.utc)
    record = to_product_price_record(row, kind=kind, read_at=read_at)
    assert record.read_at == read_at
    assert record.observation.observed_at.tzinfo is not None
    assert record.last_known_price is None
    assert set(COMMON_PROJECTION) <= row.keys()
    if kind == "device":
        assert set(DEVICE_PROJECTION) <= row.keys()
    if case == "fresh_retail_500g":
        assert record.listing.identity.quoted_unit == "CNY_PER_500G"
        assert record.observation.unit_price.amount == record.observation.current_price * 2
    if case == "device_availability":
        assert record.observation.current_price is None


@pytest.mark.parametrize(("field", "value"), [
    ("pointer_observation_id", 999), ("pointer_region_code", "999999"),
    ("revision_source_listing_id", 999), ("current_revision_id", 999),
    ("observation_quality_status", "REJECTED"), ("observation_rejection_code", "BAD"),
    ("merchant_source_channel_id", 999), ("crawl_run_source_channel_id", 999),
    ("crawl_source_listing_id", 999), ("crawl_record_id", 999),
    ("crawl_parse_status", "FAILED"), ("crawl_entity_key", "other-product"),
    ("normalizer_version", "unknown@1"), ("attribute_profile_code", "other"),
    ("match_count", 2), ("variant_catalog_item_id", 999),
    ("variant_quantity_value", Decimal(2)), ("variant_condition_code", "USED"),
    ("variant_package_count", 2), ("item_brand_id", 999),
    ("variant_attributes", {"capacity": "512GB"}),
    ("unit_price_unit", None), ("unit_price", None),
    ("current_price", 1200.0), ("current_price", True),
    ("canonical_url", "https://www.apple.com.cn.evil.invalid/"),
    ("canonical_url", "https://secret@www.apple.com.cn/"),
    ("crawl_final_url", "https://evil.invalid/"),
    ("allowed_domains", '["evil.invalid"]'),
])
def test_projection_fails_closed_without_raw_details(field: str, value: Any) -> None:
    row = _row()
    row[field] = value
    with pytest.raises(ProductPriceContractError) as error:
        to_product_price_record(row, kind="device", read_at=datetime.now(timezone.utc))
    assert str(error.value) == "价格数据不符合消费合同"
    assert error.value.__suppress_context__


def test_source_url_belongs_to_observation_crawl_not_mutable_listing_discovery() -> None:
    row = _row()
    row["canonical_url"] = "https://www.apple.com.cn/newer-discovery"
    row["crawl_final_url"] = "https://www.apple.com.cn/selected-observation"
    record = to_product_price_record(row, kind="device", read_at=datetime.now(timezone.utc))
    assert str(record.listing.source.source_url) == "https://www.apple.com.cn/selected-observation"


@pytest.mark.parametrize("case", ["fresh_retail_500g", "fresh_wholesale_kg"])
def test_shared_fresh_crawl_must_match_exact_publisher_dataset_and_source_day(case: str) -> None:
    row = _row(case)
    row["crawl_entity_key"] = "PUBLIC_PRICE:" + "a" * 64
    with pytest.raises(ProductPriceContractError):
        to_product_price_record(row, kind="fresh", read_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("native_id", [None, "", "SYNTHETIC_CUCUMBER", True, 999, "998"])
def test_mofcom_native_id_is_explicit_and_must_bind_the_same_dataset(native_id: Any) -> None:
    row = _row("fresh_wholesale_kg")
    row["source_attributes"]["source_commodity_id"] = native_id
    with pytest.raises(ProductPriceContractError):
        to_product_price_record(row, kind="fresh", read_at=datetime.now(timezone.utc))


@pytest.mark.parametrize(("field", "value"), [
    ("unit_price_unit", "CNY_PER_PIECE"), ("promotion_label", "优惠"),
    ("current_price", Decimal("1")), ("original_price", Decimal("1")),
])
def test_amountless_raw_values_are_not_silently_dropped(field: str, value: Any) -> None:
    row = _row("device_availability")
    row[field] = value
    with pytest.raises(ProductPriceContractError):
        to_product_price_record(row, kind="device", read_at=datetime.now(timezone.utc))


def test_device_reads_products_before_bounded_specs_in_one_transaction() -> None:
    async def exercise() -> None:
        row = _row()
        connection = FakeConnection([[{"catalog_item_id": 301}, {"catalog_item_id": 999}], [row, row]])
        repo = _repository(connection)
        hostile = "phone_%!' OR 1=1"
        batch = await repo.search(DevicePriceReadQuery(
            terms=(hostile,), brand_code="APPLE", category_code="PHONE",
            product_limit=1, per_product_limit=1,
        ))
        assert batch.truncated and len(batch.records) == 1
        assert connection.transaction_count == 1
        assert len(connection.calls) == 2
        products, specs = connection.calls
        assert "SELECT DISTINCT ci.id" in products[0]
        assert products[1]["product_limit"] == 2
        assert products[1]["term_0"] == "%phone!_!%!!' or 1=1%"
        assert "ESCAPE '!'" in products[0]
        assert hostile not in products[0]
        assert "iv.attributes" not in products[0]
        assert specs[1]["catalog_item_id"] == 301
        assert specs[1]["per_product_limit"] == 2
        assert "current_price IS NOT NULL" not in specs[0]
        assert "lifecycle_status =" not in specs[0]
        assert "sc.enabled" not in specs[0]
        await repo.close()

    asyncio.run(exercise())


def test_fresh_has_no_standard_catalog_dependency_and_binds_all_constraints() -> None:
    async def exercise() -> None:
        connection = FakeConnection([[_row("fresh_retail_500g")]])
        repo = _repository(connection)
        batch = await repo.search(FreshPriceReadQuery(
            terms=("黄瓜",), commodity_code="CUCUMBER",
            region=PriceRegion(scope="CITY", code="310100"),
            price_nature="RETAIL_AVERAGE", market_id="synthetic-market", listing_limit=5,
        ))
        assert len(batch.records) == 1 and not batch.truncated
        sql, params = connection.calls[0]
        assert not any(name in sql for name in ("v2_catalog_item", "v2_brand", "v2_item_variant", "v2_listing_match"))
        assert params == {"term_0": "%黄瓜%", "commodity_code": "CUCUMBER", "region_scope": "CITY",
                          "region_code": "310100", "price_nature": "RETAIL_AVERAGE",
                          "market_id": "synthetic-market", "listing_limit": 6}
        await repo.close()

    asyncio.run(exercise())


def test_initialize_checks_every_projection_even_on_empty_database() -> None:
    async def exercise() -> None:
        connection = FakeConnection([])
        repo = _repository(connection)
        repo._ready = False
        await repo.initialize()
        assert repo.readiness() == "ready"
        assert [sql for sql, _ in connection.calls] == ["SELECT 1", DEVICE_SQL + "\nLIMIT 0", FRESH_SQL + "\nLIMIT 0"]
        batch = await repo.search(FreshPriceReadQuery(terms=("不存在",)))
        assert batch.records == () and not batch.truncated
        engine = repo._engine
        await repo.close()
        assert engine.disposed  # type: ignore[union-attr]
        assert repo.readiness() == "not_ready"
        with pytest.raises(PriceRepositoryUnavailableError):
            await repo.search(FreshPriceReadQuery(terms=("黄瓜",)))

    asyncio.run(exercise())


@pytest.mark.parametrize(("code", "expected"), [(1054, "contract_error"), (1146, "contract_error"), (1045, "not_ready")])
def test_initialize_redacts_schema_and_connectivity_failures(code: int, expected: str) -> None:
    class Broken(FakeConnection):
        def execute(self, statement: object, parameters: dict[str, Any]) -> FakeResult:
            raise OperationalError("private SQL", {"password": "never-print"}, Exception(code, "private host"))

    async def exercise() -> None:
        repo = _repository(Broken([]))
        repo._ready = False
        await repo.initialize()
        assert repo.readiness() == expected
        with pytest.raises((PriceRepositoryUnavailableError, ProductPriceContractError)) as error:
            await repo.search(FreshPriceReadQuery(terms=("黄瓜",)))
        assert "private" not in str(error.value)
        await repo.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [False, True])
def test_timed_out_or_cancelled_worker_holds_capacity_until_thread_really_stops(cancel: bool) -> None:
    started = threading.Event()
    release = threading.Event()

    class Blocking(FakeConnection):
        def execute(self, statement: object, parameters: dict[str, Any]) -> FakeResult:
            self.calls.append((str(statement), parameters))
            started.set()
            assert release.wait(2)
            return FakeResult([])

    async def exercise() -> None:
        connection = Blocking([])
        repo = _repository(connection, timeout=0.05)
        query = FreshPriceReadQuery(terms=("黄瓜",))
        first = asyncio.create_task(repo.search(query))
        while not started.is_set():
            await asyncio.sleep(0.001)
        if cancel:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            with pytest.raises(ProductPriceTimeoutError):
                await first
        assert repo._query_capacity.locked()
        assert len(repo._workers) == 1
        with pytest.raises(ProductPriceTimeoutError):
            await repo.search(query)
        assert len(connection.calls) == 1
        closer = asyncio.create_task(repo.close())
        await asyncio.sleep(0.005)
        assert not closer.done()
        release.set()
        await closer
        assert not repo._workers

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_read_timestamp_is_sampled_after_transaction_not_before_query_wait() -> None:
    class Slow(FakeConnection):
        def execute(self, statement: object, parameters: dict[str, Any]) -> FakeResult:
            time.sleep(0.01)
            self.finished = datetime.now(timezone.utc)
            return super().execute(statement, parameters)

    async def exercise() -> None:
        connection = Slow([[_row("fresh_retail_500g")]])
        repo = _repository(connection)
        batch = await repo.search(FreshPriceReadQuery(terms=("黄瓜",)))
        assert batch.records[0].read_at >= connection.finished
        await repo.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("overrides", [
    {"pool_size": True}, {"pool_size": 0}, {"pool_size": 1.5},
    {"connect_timeout_seconds": float("nan")}, {"query_timeout_seconds": float("inf")},
    {"query_timeout_seconds": False}, {"query_timeout_seconds": 0},
    {"dsn": "sqlite://"}, {"dsn": "postgresql://private:secret@internal/private"},
])
def test_configuration_rejects_unbounded_or_wrong_database_inputs(overrides: dict[str, Any]) -> None:
    options = {"dsn": "mysql+pymysql://unused", "pool_size": 1,
               "connect_timeout_seconds": 1, "query_timeout_seconds": 1} | overrides
    with pytest.raises(ValueError) as error:
        MySQLProductPriceRepository(**options)
    assert "secret" not in str(error.value)


def test_repository_cannot_be_called_with_an_unvalidated_unbounded_query() -> None:
    async def exercise() -> None:
        connection = FakeConnection([])
        repo = _repository(connection)
        query = DevicePriceReadQuery.model_construct(terms=("phone",), product_limit=100000)
        with pytest.raises(ProductPriceContractError, match="查询条件无效"):
            await repo.search(query)
        assert connection.calls == []
        await repo.close()

    asyncio.run(exercise())


def test_duplicate_fact_batch_is_a_safe_contract_error_not_pydantic_row_dump() -> None:
    async def exercise() -> None:
        row = _row("fresh_retail_500g")
        repo = _repository(FakeConnection([[row, row]]))
        with pytest.raises(ProductPriceContractError) as error:
            await repo.search(FreshPriceReadQuery(terms=("黄瓜",)))
        assert "https" not in str(error.value)
        assert error.value.__suppress_context__
        await repo.close()

    asyncio.run(exercise())


def test_cancelled_close_waiter_does_not_abandon_or_duplicate_pool_cleanup() -> None:
    started = threading.Event()
    release = threading.Event()

    class Blocking(FakeConnection):
        def execute(self, statement: object, parameters: dict[str, Any]) -> FakeResult:
            started.set()
            assert release.wait(2)
            return FakeResult([])

    async def exercise() -> None:
        repo = _repository(Blocking([]))
        engine = repo._engine
        query = asyncio.create_task(repo.search(FreshPriceReadQuery(terms=("黄瓜",))))
        while not started.is_set():
            await asyncio.sleep(0.001)
        closer = asyncio.create_task(repo.close())
        await asyncio.sleep(0.001)
        closer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closer
        assert not engine.disposed  # type: ignore[union-attr]
        release.set()
        with pytest.raises(ProductPriceTimeoutError):
            await query
        await asyncio.gather(repo.close(), repo.close())
        assert engine.disposed  # type: ignore[union-attr]
        assert not repo._workers

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_sql_relations_are_v2_only_and_located_contradictions_are_not_filtered() -> None:
    import re

    for sql in (DEVICE_SQL, FRESH_SQL):
        tables = re.findall(r"\b(?:FROM|JOIN)\s+(\w+)", sql)
        assert tables and all(table.startswith("v2_") for table in tables)
        assert "po.region_code = pc.region_code" not in sql
        assert "po.quality_status = 'ACCEPTED'" not in sql
        assert "JOIN v2_price_observation" in sql
    assert "LEFT JOIN v2_crawl_record" in DEVICE_SQL
