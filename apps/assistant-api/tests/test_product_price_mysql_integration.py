"""Real MySQL checks, skipped unless the disposable local runner opts in."""

from __future__ import annotations

import asyncio
import json
from datetime import timezone
from decimal import Decimal
from hashlib import sha256

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from .product_price_mysql_fixture import PriceMySQLFixture, V1_TABLES, V2_TABLES, guarded_test_dsns, price_mysql
from spb_assistant_api.adapters.mysql_product_price import MySQLProductPriceRepository
from spb_assistant_api.domain.exceptions import ProductPriceContractError
from spb_assistant_api.domain.product_price import PriceRegion
from spb_assistant_api.domain.product_price_query import DevicePriceReadQuery, FreshPriceReadQuery


def _repository(database: PriceMySQLFixture) -> MySQLProductPriceRepository:
    return MySQLProductPriceRepository(
        dsn=database.reader_dsn, pool_size=1, connect_timeout_seconds=3, query_timeout_seconds=10,
    )


def _search(database: PriceMySQLFixture, query):
    async def run():
        repository = _repository(database)
        try:
            await repository.initialize()
            assert repository.readiness() == "ready"
            return await repository.search(query)
        finally:
            await repository.close()
    return asyncio.run(run())


def test_v2_only_schema_supports_device_fresh_and_reader_cannot_write(price_mysql):
    with price_mysql.writer.connect() as connection:
        assert set(connection.execute(text("SHOW TABLES")).scalars()) == set(V2_TABLES)
    assert {record.pointer.source_listing_id for record in _search(
        price_mysql, DevicePriceReadQuery(terms=("Alpha",)),
    ).records} == {101, 104}
    assert {record.pointer.source_listing_id for record in _search(
        price_mysql, FreshPriceReadQuery(terms=("合成",)),
    ).records} == {102, 103}
    # This uses a plain reader connection, not the Repository's read-only session:
    # denial therefore proves database privileges, not merely a session flag.
    with price_mysql.reader.connect() as connection, pytest.raises(DBAPIError) as error:
        connection.execute(text("UPDATE v2_price_observation SET current_price=1 WHERE id=501"))
    assert error.value.orig.args[0] == 1142


def test_old_tables_present_but_forbidden_do_not_change_v2_queries(price_mysql):
    for table in V1_TABLES:
        price_mysql.execute(f"CREATE TABLE `{table}` (id BIGINT PRIMARY KEY)")
        with price_mysql.reader.connect() as connection, pytest.raises(DBAPIError) as error:
            connection.execute(text(f"SELECT * FROM `{table}` LIMIT 1"))
        assert error.value.orig.args[0] == 1142
    assert len(_search(price_mysql, DevicePriceReadQuery(terms=("Alpha",))).records) == 2
    assert len(_search(price_mysql, FreshPriceReadQuery(terms=("合成",))).records) == 2


def test_exact_amounts_amountless_state_and_source_day_survive_native_mysql(price_mysql):
    device = _search(price_mysql, DevicePriceReadQuery(terms=("16",))).records[0]
    assert device.observation.current_price == Decimal("1200.00")
    assert device.observation.original_price == Decimal("1500.00")
    assert device.observation.observed_at.tzinfo == timezone.utc
    assert device.read_at >= device.observation.observed_at
    state = _search(price_mysql, DevicePriceReadQuery(terms=("Watch",))).records[0]
    assert state.observation.kind == "availability_only"
    assert state.observation.current_price is None
    assert state.observation.availability == "OFF_SHELF"
    # No unreviewed history query is silently added in B, even though the
    # synthetic database contains a previous priced observation for this row.
    assert state.last_known_price is None
    fresh = _search(price_mysql, FreshPriceReadQuery(terms=("黄瓜",))).records[0]
    assert fresh.observation.current_price == Decimal("3.50")
    assert fresh.listing.identity.quoted_unit == "CNY_PER_500G"
    assert fresh.observation.unit_price.amount == Decimal("7.000000")
    assert fresh.observation.unit_price.unit == "CNY_PER_KG"
    assert fresh.observation.observed_at.isoformat() == "2026-01-15T16:00:00+00:00"


def test_fresh_filter_uses_source_region_market_and_nature_without_catalog_join(price_mysql):
    query = FreshPriceReadQuery(
        terms=("鸡蛋",), commodity_code="SYNTHETIC_EGG",
        region=PriceRegion(scope="PROVINCE", code="XJ_CORPS"),
        price_nature="WHOLESALE_AVERAGE", market_id="synthetic-market",
    )
    result = _search(price_mysql, query)
    assert len(result.records) == 1
    assert result.records[0].listing.identity.source_market_name == "合成市场"
    assert _search(price_mysql, query.model_copy(update={"market_id": "no-such-market"})).records == ()
    assert _search(price_mysql, query.model_copy(update={"price_nature": "RETAIL_AVERAGE"})).records == ()


def test_empty_v2_projection_is_ready_not_a_database_outage(price_mysql):
    price_mysql.execute("DELETE FROM v2_price_current")
    assert _search(price_mysql, DevicePriceReadQuery(terms=("Alpha",))).records == ()
    assert _search(price_mysql, FreshPriceReadQuery(terms=("合成",))).records == ()


@pytest.mark.parametrize("statement", [
    "UPDATE v2_price_current SET region_code='WRONG' WHERE id=101",
    "UPDATE v2_price_current SET listing_revision_id=204 WHERE id=101",
    "UPDATE v2_source_listing SET current_revision_id=204 WHERE id=101",
    "UPDATE v2_listing_revision SET source_listing_id=104 WHERE id=201",
    "UPDATE v2_price_observation SET source_listing_id=104 WHERE id=501",
    "UPDATE v2_price_observation SET observed_at='2026-01-15 00:00:00' WHERE id=501",
    "UPDATE v2_price_observation SET quality_status='REJECTED',rejection_code='SYNTHETIC' WHERE id=501",
    "UPDATE v2_price_observation SET rejection_code='SYNTHETIC' WHERE id=501",
    "UPDATE v2_listing_revision SET rejection_code='SYNTHETIC' WHERE id=201",
    "UPDATE v2_merchant SET source_channel_id=12 WHERE id=11",
    "UPDATE v2_crawl_run SET source_channel_id=12 WHERE id=601",
    "UPDATE v2_crawl_record SET source_listing_id=104 WHERE id=601",
    "UPDATE v2_crawl_record SET entity_key='different-product' WHERE id=601",
    "UPDATE v2_crawl_record SET parse_status='FAILED' WHERE id=601",
])
def test_locatable_broken_current_relations_are_contract_errors(price_mysql, statement):
    price_mysql.execute(statement)
    with pytest.raises(ProductPriceContractError):
        # Alpha locates both synthetic identities even when a pointer incorrectly
        # refers to the other product's revision. This is not a full orphan scan.
        _search(price_mysql, DevicePriceReadQuery(terms=("Alpha",)))


@pytest.mark.parametrize("statement", [
    "UPDATE v2_listing_match SET match_status='REJECTED' WHERE id=201",
    "UPDATE v2_listing_match SET effective_to='2026-01-16 00:00:00' WHERE id=201",
    "UPDATE v2_listing_match SET effective_from='2099-01-01 00:00:00' WHERE id=201",
])
def test_ineligible_matches_are_not_returned(price_mysql, statement):
    price_mysql.execute(statement)
    assert _search(price_mysql, DevicePriceReadQuery(terms=("16",))).records == ()


@pytest.mark.parametrize("statement", [
    "UPDATE v2_source_channel SET allowed_domains=JSON_ARRAY('untrusted.invalid') WHERE id=11",
    "UPDATE v2_source_listing SET canonical_url='https://untrusted.invalid/price' WHERE id=101",
    "UPDATE v2_crawl_record SET final_url='https://untrusted.invalid/price' WHERE id=601",
    "UPDATE v2_source_channel SET source_type='PUBLIC_DATA' WHERE id=11",
    "UPDATE v2_merchant SET verification_status='UNVERIFIED' WHERE id=11",
    "UPDATE v2_listing_revision SET normalizer_version='unreviewed@2' WHERE id=201",
    "UPDATE v2_item_variant SET attributes=JSON_OBJECT('capacity','512GB') WHERE id=401",
    "UPDATE v2_price_observation SET original_price=1000 WHERE id=501",
    "UPDATE v2_price_observation SET unit_price_unit=NULL WHERE id=501",
])
def test_invalid_returned_facts_fail_closed_instead_of_repairing_data(price_mysql, statement):
    price_mysql.execute(statement)
    with pytest.raises(ProductPriceContractError):
        _search(price_mysql, DevicePriceReadQuery(terms=("16",)))


def test_duplicate_active_matches_are_an_error_but_expired_matches_are_ignored(price_mysql):
    price_mysql.clone("v2_listing_match", 201, 299, effective_to="2026-01-16 00:00:00")
    assert len(_search(price_mysql, DevicePriceReadQuery(terms=("16",))).records) == 1
    price_mysql.execute("UPDATE v2_listing_match SET effective_to=NULL WHERE id=299")
    with pytest.raises(ProductPriceContractError):
        _search(price_mysql, DevicePriceReadQuery(terms=("16",)))


def test_capture_switches_and_partial_runs_do_not_hide_accepted_current_facts(price_mysql):
    price_mysql.execute("UPDATE v2_source_channel SET enabled=0")
    price_mysql.execute("UPDATE v2_crawl_run SET status='PARTIAL'")
    price_mysql.execute("UPDATE v2_crawl_record SET validation_status='FAILED'")
    assert len(_search(price_mysql, DevicePriceReadQuery(terms=("Alpha",))).records) == 2
    assert len(_search(price_mysql, FreshPriceReadQuery(terms=("合成",))).records) == 2


@pytest.mark.parametrize("term", ["%", "_", "' OR 1=1 --", "256GB"])
def test_device_search_binds_literals_and_does_not_use_sku_specs_as_model_names(price_mysql, term):
    assert _search(price_mysql, DevicePriceReadQuery(terms=(term,))).records == ()


def test_stable_order_and_product_then_sku_limits_are_visible(price_mysql):
    first = _search(price_mysql, DevicePriceReadQuery(terms=("Alpha",), product_limit=1))
    assert [record.pointer.source_listing_id for record in first.records] == [101]
    assert first.truncated
    attributes = json.dumps({"capacity": "512GB", "color": "合成颜色"}, ensure_ascii=False)
    price_mysql.clone("v2_item_variant", 401, 405, attributes=attributes, variant_key="synthetic-extra")
    price_mysql.clone("v2_source_listing", 101, 105, current_revision_id=205)
    price_mysql.clone("v2_listing_revision", 201, 205, source_listing_id=105, normalized_attributes=attributes)
    price_mysql.clone("v2_listing_match", 201, 205, listing_revision_id=205, item_variant_id=405)
    price_mysql.clone("v2_price_observation", 501, 505, source_listing_id=105, listing_revision_id=205)
    price_mysql.clone("v2_price_current", 101, 105, source_listing_id=105, listing_revision_id=205, price_observation_id=505)
    query = DevicePriceReadQuery(terms=("Alpha",), per_product_limit=1)
    for _ in range(2):
        batch = _search(price_mysql, query)
        # One product's many SKUs must not consume another product's budget.
        assert [record.pointer.source_listing_id for record in batch.records] == [101, 104]
        assert batch.truncated
    fresh = _search(price_mysql, FreshPriceReadQuery(terms=("合成",), listing_limit=1))
    assert fresh.truncated and [record.pointer.source_listing_id for record in fresh.records] == [103]


def test_readiness_checks_required_projection_not_just_select_one(price_mysql):
    price_mysql.execute("ALTER TABLE v2_source_channel CHANGE currency missing_currency VARCHAR(3) NOT NULL")
    async def check():
        repository = _repository(price_mysql)
        try:
            await repository.initialize()
            assert repository.readiness() == "contract_error"
            with pytest.raises(ProductPriceContractError):
                await repository.search(DevicePriceReadQuery(terms=("Alpha",)))
        finally:
            await repository.close()
    try:
        asyncio.run(check())
    finally:
        price_mysql.execute("ALTER TABLE v2_source_channel CHANGE missing_currency currency VARCHAR(3) NOT NULL")


def test_multiple_statements_use_one_repeatable_read_snapshot(price_mysql):
    price_mysql.clone(
        "v2_price_observation", 501, 551, current_price="1300.00", unit_price="1300.000000",
        observed_at="2026-01-16 04:00:00",
    )
    async def check():
        repository = _repository(price_mysql)
        updated = False
        try:
            await repository.initialize()
            assert repository.readiness() == "ready"
            assert repository._engine is not None

            @event.listens_for(repository._engine, "after_cursor_execute")
            def advance_pointer_after_product_discovery(connection, cursor, statement, parameters, context, executemany):
                nonlocal updated
                if updated or not statement.lstrip().startswith("SELECT DISTINCT ci.id"):
                    return
                updated = True
                # An independent writer commits after the first statement's
                # snapshot. A read-committed / split-transaction implementation
                # would incorrectly combine product discovery with new facts.
                price_mysql.execute(
                    "UPDATE v2_price_current SET price_observation_id=551, "
                    "observed_at='2026-01-16 04:00:00' WHERE id=101"
                )

            query = DevicePriceReadQuery(terms=("16",))
            before = await repository.search(query)
            assert updated, "the concurrent writer must actually run during the read"
            assert before.records[0].pointer.price_observation_id == 501
            assert before.records[0].observation.current_price == Decimal("1200.00")
            after = await repository.search(query)
            assert after.records[0].pointer.price_observation_id == 551
            assert after.records[0].observation.current_price == Decimal("1300.00")
        finally:
            await repository.close()
    asyncio.run(check())


@pytest.mark.parametrize("statement", [
    "UPDATE v2_listing_revision SET source_attributes=JSON_REMOVE(source_attributes,'$.source_commodity_id') WHERE id=203",
    "UPDATE v2_listing_revision SET source_attributes=JSON_SET(source_attributes,'$.source_commodity_id','1000') WHERE id=203",
    "UPDATE v2_listing_revision SET source_attributes=JSON_SET(source_attributes,'$.source_commodity_id',0) WHERE id=203",
])
def test_fresh_shared_evidence_requires_explicit_matching_native_commodity_id(price_mysql, statement):
    price_mysql.execute(statement)
    with pytest.raises(ProductPriceContractError):
        _search(price_mysql, FreshPriceReadQuery(terms=("鸡蛋",)))


def test_fresh_shared_report_cannot_be_from_another_source_day(price_mysql):
    wrong_key = "PUBLIC_PRICE:" + sha256(b"shanghai-fresh-retail:2026-01-15").hexdigest()
    price_mysql.execute("UPDATE v2_crawl_record SET entity_key=:entity_key WHERE id=602", entity_key=wrong_key)
    with pytest.raises(ProductPriceContractError):
        _search(price_mysql, FreshPriceReadQuery(terms=("黄瓜",)))


def test_source_link_belongs_to_observation_evidence_not_mutable_listing_url(price_mysql):
    evidence_url = "https://fgw.sh.gov.cn/synthetic/price-query/2026-01-16"
    price_mysql.execute("UPDATE v2_crawl_record SET final_url=:url WHERE id=602", url=evidence_url)
    price_mysql.execute(
        "UPDATE v2_source_listing SET canonical_url='https://fgw.sh.gov.cn/synthetic/latest' WHERE id=102"
    )
    record = _search(price_mysql, FreshPriceReadQuery(terms=("黄瓜",))).records[0]
    assert str(record.listing.source.source_url) == evidence_url


@pytest.mark.parametrize("unsafe", [
    "mysql+pymysql://price_writer:synthetic@remote.invalid:3306/price_query_test_aaaaaaaaaaaaaaaaaaaa?charset=utf8mb4",
    "mysql+pymysql://price_writer:synthetic@127.0.0.1:3306/company_data?charset=utf8mb4",
    "mysql+pymysql://root:synthetic@127.0.0.1:3306/price_query_test_aaaaaaaaaaaaaaaaaaaa?charset=utf8mb4",
    "mysql+pymysql://price_writer:synthetic@127.0.0.1:3306/price_query_test_aaaaaaaaaaaaaaaaaaaa?unix_socket=/tmp/mysql.sock",
])
def test_test_database_guard_rejects_unsafe_targets_without_connecting(monkeypatch, unsafe):
    monkeypatch.setenv("RUN_PRODUCT_PRICE_MYSQL_TESTS", "1")
    monkeypatch.setenv("PRICE_QUERY_TEST_RUN_ID", "a" * 20)
    monkeypatch.setenv("PRICE_QUERY_TEST_WRITER_DSN", unsafe)
    monkeypatch.setenv(
        "PRICE_QUERY_TEST_READER_DSN",
        "mysql+pymysql://price_reader:reader@127.0.0.1:3306/price_query_test_aaaaaaaaaaaaaaaaaaaa?charset=utf8mb4",
    )
    with pytest.raises(ValueError, match="isolated loopback"):
        guarded_test_dsns()


def test_test_database_guard_ignores_application_dsn_and_requires_opt_in(monkeypatch):
    monkeypatch.delenv("RUN_PRODUCT_PRICE_MYSQL_TESTS", raising=False)
    monkeypatch.setenv("ASSISTANT_MYSQL_DSN", "mysql+pymysql://synthetic.invalid/company")
    with pytest.raises(pytest.skip.Exception, match="explicit isolated"):
        guarded_test_dsns()
