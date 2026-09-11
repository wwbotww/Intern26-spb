"""Synthetic rows and guarded writer fixture; never use application database config."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url


FIXTURES = Path(__file__).parent / "fixtures/product_price"
V2_TABLES = tuple(re.findall(r"CREATE TABLE (v2_[a-z_]+)\s*\(", (FIXTURES / "mysql_schema.sql").read_text()))
V1_TABLES = ("price_current", "official_offer", "sku", "product", "brand", "sales_channel")
DOMAINS = {
    "APPLE_CN_WEB": "www.apple.com.cn",
    "SH_FGW_FRESH_RETAIL": "fgw.sh.gov.cn",
    "MOFCOM_FRESH_WHOLESALE": "cif.mofcom.gov.cn",
}


def guarded_test_dsns() -> tuple[str, str]:
    """Fail before any connection or mutation unless the disposable target matches."""
    if os.environ.get("RUN_PRODUCT_PRICE_MYSQL_TESTS") != "1":
        pytest.skip("explicit isolated MySQL runner required")
    token = os.environ.get("PRICE_QUERY_TEST_RUN_ID", "")
    if not re.fullmatch(r"[a-f0-9]{20}", token):
        raise ValueError("isolated runner token required")
    raw_values = tuple(os.environ.get(f"PRICE_QUERY_TEST_{role}_DSN", "") for role in ("WRITER", "READER"))
    try:
        writer, reader = (make_url(raw) for raw in raw_values)
        for role, url in (("writer", writer), ("reader", reader)):
            if not (
                url.drivername == "mysql+pymysql" and url.host == "127.0.0.1"
                and url.port and 1024 < url.port < 65536
                and url.database == f"price_query_test_{token}"
                and url.username == f"price_{role}" and url.password
                and dict(url.query) == {"charset": "utf8mb4"}
            ):
                raise ValueError("not a dedicated synthetic target")
        if (writer.host, writer.port, writer.database) != (reader.host, reader.port, reader.database):
            raise ValueError("reader and writer target different test databases")
        if writer.password == reader.password:
            raise ValueError("test roles must have distinct credentials")
    except Exception:
        # URL parser/DB errors must not echo caller-supplied credentials.
        raise ValueError("only this run's isolated loopback MySQL test DSNs are accepted") from None
    return raw_values  # type: ignore[return-value]


@dataclass
class PriceMySQLFixture:
    writer: Engine
    reader: Engine
    reader_dsn: str

    def execute(self, sql: str, **parameters: Any) -> None:
        with self.writer.begin() as connection:
            connection.execute(text(sql), parameters)

    def insert(self, table: str, values: dict[str, Any]) -> None:
        if table not in V2_TABLES or not all(re.fullmatch(r"[a-z_]+", key) for key in values):
            raise ValueError("only known synthetic V2 tables and columns may be inserted")
        columns = ", ".join(f"`{key}`" for key in values)
        parameters = ", ".join(f":{key}" for key in values)
        with self.writer.begin() as connection:
            connection.execute(text(f"INSERT INTO `{table}` ({columns}) VALUES ({parameters})"), values)

    def clone(self, table: str, source_id: int, new_id: int, **changes: Any) -> None:
        if table not in V2_TABLES:
            raise ValueError("only known synthetic V2 rows may be cloned")
        with self.writer.connect() as connection:
            row = dict(connection.execute(text(f"SELECT * FROM `{table}` WHERE id=:id"), {"id": source_id}).mappings().one())
        row.update(id=new_id, **changes)
        self.insert(table, row)


def _utc_naive(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def seed_records(database: PriceMySQLFixture) -> None:
    """Adapt the reviewed synthetic fact fixture into independently seeded SQL rows."""
    records = json.loads((FIXTURES / "read_records.json").read_text())
    inserted_sources: set[int] = set()
    inserted_brands: set[str] = set()
    for raw in records.values():
        listing, observation = raw["listing"], raw["observation"]
        identity, source = listing["identity"], listing["source"]
        listing_id, revision_id = listing["source_listing_id"], listing["listing_revision_id"]
        source_id = source["source_channel_id"]
        domain = DOMAINS[source["channel_code"]]
        source_url = f"https://{domain}/synthetic/price-query/{listing_id}"
        if source_id not in inserted_sources:
            database.insert("v2_source_channel", {
                "id": source_id, "code": source["channel_code"], "name": source["channel_name"],
                "source_type": source["source_type"], "business_mode": source["business_mode"],
                "allowed_domains": json.dumps([domain]), "currency": "CNY", "enabled": 1,
            })
            database.insert("v2_merchant", {
                "id": source_id, "source_channel_id": source_id, "name": "合成卖家",
                "seller_type": source["seller_type"], "verification_status": source["verification_status"],
                "status": "ACTIVE",
            })
            inserted_sources.add(source_id)
        is_device = identity["kind"] == "device"
        if is_device:
            if identity["brand_code"] not in inserted_brands:
                database.insert("v2_brand", {
                    "id": 1, "code": identity["brand_code"], "name_zh": identity["brand_name"], "status": "ACTIVE",
                })
                inserted_brands.add(identity["brand_code"])
            category_id = identity["catalog_item_id"]
            database.insert("v2_category", {
                "id": category_id, "code": identity["category_code"], "attribute_profile_code": "electronic-device",
                "attribute_profile_version": "1", "enabled": 1,
            })
            database.insert("v2_catalog_item", {
                "id": identity["catalog_item_id"], "category_id": category_id, "brand_id": 1,
                "canonical_key": f"synthetic-{category_id}", "item_type": "MODEL",
                "name": identity["product_name"], "series_name": identity.get("series_name"),
                "model_number": identity.get("model_number"), "base_attributes": "{}", "status": "ACTIVE",
            })
            database.insert("v2_item_variant", {
                "id": identity["item_variant_id"], "catalog_item_id": identity["catalog_item_id"],
                "variant_key": f"synthetic-{revision_id}", "name": identity["product_name"],
                "manufacturer_part_number": identity["specification"].get("manufacturer_part_number"),
                "condition_code": "NEW", "measure_type": "COUNT", "quantity_value": "1",
                "base_unit": "PIECE", "package_count": 1,
                "attributes": json.dumps(identity["specification"], ensure_ascii=False), "status": "ACTIVE",
            })
            database.insert("v2_listing_match", {
                "id": revision_id, "listing_revision_id": revision_id,
                "item_variant_id": identity["item_variant_id"], "match_status": "ACCEPTED",
                "effective_from": _utc_naive(identity["match"]["effective_from"]), "effective_to": None,
            })
            normalized = identity["specification"]
        else:
            normalized = {key: identity[key] for key in (
                "commodity_code", "commodity_name", "commodity_group", "source_specification", "quoted_unit",
            )}
        database.insert("v2_source_listing", {
            "id": listing_id, "source_channel_id": source_id, "merchant_id": source_id,
            "external_product_id": identity.get("official_product_id"), "external_sku_id": identity.get("official_sku_id"),
            "price_nature": listing["price_nature"], "canonical_url": source_url,
            "current_revision_id": revision_id, "lifecycle_status": "INACTIVE" if observation["kind"] == "availability_only" else "ACTIVE",
        })
        source_attributes: dict[str, Any] = {"time_precision": observation["time_precision"]}
        if source["channel_code"] == "MOFCOM_FRESH_WHOLESALE":
            # An explicit synthetic upstream commodity ID, not inferred from
            # the consumer's normalized commodity code.
            source_attributes["source_commodity_id"] = "999"
        for suffix in ("id", "name"):
            if identity.get(f"source_market_{suffix}"):
                source_attributes[f"source_market_{suffix}"] = identity[f"source_market_{suffix}"]
        database.insert("v2_listing_revision", {
            "id": revision_id, "source_listing_id": listing_id,
            "first_crawl_record_id": observation["crawl_record_id"],
            "source_title": identity.get("product_name", identity.get("commodity_name")),
            "source_attributes": json.dumps(source_attributes, ensure_ascii=False),
            "normalized_attributes": json.dumps(normalized, ensure_ascii=False),
            "condition_code": "NEW" if is_device else "UNKNOWN",
            "measure_type": "COUNT" if is_device else "WEIGHT",
            "base_quantity_value": listing["base_quantity_value"], "base_unit": listing["base_unit"],
            "package_count": 1, "normalizer_version": "electronic-device@1" if is_device else "government-fresh@1",
            "quality_status": "ACCEPTED", "rejection_code": None,
        })
        observations = [observation] + ([raw["last_known_price"]] if raw["last_known_price"] else [])
        for fact in observations:
            crawl_id = fact["crawl_record_id"]
            if is_device:
                entity_key = identity["official_product_id"]
            else:
                source_day = datetime.fromisoformat(fact["observed_at"].replace("Z", "+00:00")).astimezone(
                    ZoneInfo("Asia/Shanghai")
                ).date().isoformat()
                dataset_key = (
                    f"shanghai-fresh-retail:{source_day}"
                    if source["channel_code"] == "SH_FGW_FRESH_RETAIL"
                    else f"mofcom-bj:{source_attributes['source_commodity_id']}:{source_day}"
                )
                entity_key = "PUBLIC_PRICE:" + sha256(dataset_key.encode()).hexdigest()
            database.insert("v2_crawl_run", {
                "id": crawl_id, "source_channel_id": source_id, "status": "SUCCEEDED",
                "started_at": _utc_naive(fact["observed_at"]), "finished_at": _utc_naive(fact["observed_at"]),
            })
            database.insert("v2_crawl_record", {
                "id": crawl_id, "crawl_run_id": crawl_id, "source_listing_id": None,
                "entity_type": "PRODUCT" if is_device else "PUBLIC_PRICE",
                "entity_key": entity_key,
                "request_url": source_url, "final_url": source_url,
                "fetch_status": "SUCCEEDED", "parse_status": "SUCCEEDED", "validation_status": "SUCCEEDED",
                "fetched_at": _utc_naive(fact["observed_at"]),
            })
            values = {key: fact.get(key) for key in (
                "source_listing_id", "listing_revision_id", "crawl_record_id", "currency", "original_price",
                "original_price_type", "current_price", "price_nature", "price_type", "pricing_basis",
                "promotion_label", "availability", "fee_status", "quality_status",
            )}
            unit_price = fact.get("unit_price")
            values.update(
                id=fact["observation_id"], region_scope=fact["region"]["scope"], region_code=fact["region"]["code"],
                unit_price=unit_price["amount"] if unit_price else None,
                unit_price_unit=unit_price["unit"] if unit_price else None,
                observed_at=_utc_naive(fact["observed_at"]), rejection_code=None,
            )
            database.insert("v2_price_observation", values)
        pointer = raw["pointer"]
        database.insert("v2_price_current", {
            "id": listing_id, "source_listing_id": listing_id, "listing_revision_id": revision_id,
            "price_observation_id": pointer["price_observation_id"], "region_scope": pointer["region"]["scope"],
            "region_code": pointer["region"]["code"], "observed_at": _utc_naive(pointer["observed_at"]),
        })


@pytest.fixture
def price_mysql():
    writer_dsn, reader_dsn = guarded_test_dsns()
    writer = create_engine(writer_dsn, hide_parameters=True, pool_pre_ping=True)
    reader = create_engine(reader_dsn, hide_parameters=True, pool_pre_ping=True)
    fixture = PriceMySQLFixture(writer=writer, reader=reader, reader_dsn=reader_dsn)
    try:
        for table in V1_TABLES:
            fixture.execute(f"DROP TABLE IF EXISTS `{table}`")
        for table in reversed(V2_TABLES):
            fixture.execute(f"DELETE FROM `{table}`")
        seed_records(fixture)
        yield fixture
    finally:
        writer.dispose()
        reader.dispose()
