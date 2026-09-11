"""Bounded, consistent, V2-only MySQL reads; not wired into production yet."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, TypeVar

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from ..domain.exceptions import (
    PriceRepositoryError,
    PriceRepositoryUnavailableError,
    ProductPriceContractError,
    ProductPriceTimeoutError,
)
from ..domain.product_price_query import (
    DevicePriceReadQuery,
    PRODUCT_PRICE_READ_QUERY,
    PriceReadBatch,
    ProductPriceReadQuery,
)
from .product_price_rows import to_product_price_record


T = TypeVar("T")

# Expressions are source controlled. Query values, even LIKE patterns and
# limits, are bound parameters; no user-provided table, column or JSON path.
COMMON_PROJECTION = {
    "source_listing_id": "sl.id",
    "listing_revision_id": "lr.id",
    "revision_source_listing_id": "lr.source_listing_id",
    "current_revision_id": "sl.current_revision_id",
    "listing_price_nature": "sl.price_nature",
    "revision_quality_status": "lr.quality_status",
    "revision_rejection_code": "lr.rejection_code",
    "base_quantity_value": "lr.base_quantity_value",
    "base_quantity_min": "lr.base_quantity_min",
    "base_quantity_max": "lr.base_quantity_max",
    "base_unit": "lr.base_unit",
    "revision_measure_type": "lr.measure_type",
    "revision_condition_code": "lr.condition_code",
    "revision_package_count": "lr.package_count",
    "source_attributes": "lr.source_attributes",
    "normalized_attributes": "lr.normalized_attributes",
    "normalizer_version": "lr.normalizer_version",
    "canonical_url": "sl.canonical_url",
    "source_channel_id": "sc.id",
    "source_currency": "sc.currency",
    "channel_code": "sc.code",
    "channel_name": "sc.name",
    "source_type": "sc.source_type",
    "business_mode": "sc.business_mode",
    "allowed_domains": "sc.allowed_domains",
    "merchant_id": "m.id",
    "listing_merchant_id": "sl.merchant_id",
    "merchant_source_channel_id": "m.source_channel_id",
    "seller_type": "m.seller_type",
    "verification_status": "m.verification_status",
    "pointer_source_listing_id": "pc.source_listing_id",
    "pointer_listing_revision_id": "pc.listing_revision_id",
    "pointer_observation_id": "pc.price_observation_id",
    "pointer_region_scope": "pc.region_scope",
    "pointer_region_code": "pc.region_code",
    "pointer_observed_at": "pc.observed_at",
    "observation_id": "po.id",
    "observation_source_listing_id": "po.source_listing_id",
    "observation_listing_revision_id": "po.listing_revision_id",
    "observation_region_scope": "po.region_scope",
    "observation_region_code": "po.region_code",
    "observation_observed_at": "po.observed_at",
    "observation_quality_status": "po.quality_status",
    "observation_rejection_code": "po.rejection_code",
    "crawl_record_id": "po.crawl_record_id",
    "currency": "po.currency",
    "current_price": "po.current_price",
    "original_price": "po.original_price",
    "original_price_type": "po.original_price_type",
    "price_nature": "po.price_nature",
    "price_type": "po.price_type",
    "pricing_basis": "po.pricing_basis",
    "availability": "po.availability",
    "unit_price": "po.unit_price",
    "unit_price_unit": "po.unit_price_unit",
    "fee_status": "po.fee_status",
    "promotion_label": "po.promotion_label",
    "crawl_id": "cr.id",
    "crawl_source_listing_id": "cr.source_listing_id",
    "record_crawl_run_id": "cr.crawl_run_id",
    "crawl_run_id": "run.id",
    "crawl_run_source_channel_id": "run.source_channel_id",
    "crawl_entity_type": "cr.entity_type",
    "crawl_entity_key": "cr.entity_key",
    "crawl_request_url": "cr.request_url",
    "crawl_final_url": "cr.final_url",
    "crawl_fetch_status": "cr.fetch_status",
    "crawl_parse_status": "cr.parse_status",
}

DEVICE_PROJECTION = {
    "catalog_item_id": "ci.id",
    "item_type": "ci.item_type",
    "item_brand_id": "ci.brand_id",
    "item_category_id": "ci.category_id",
    "category_id": "cat.id",
    "category_code": "cat.code",
    "attribute_profile_code": "cat.attribute_profile_code",
    "attribute_profile_version": "cat.attribute_profile_version",
    "brand_id": "b.id",
    "brand_code": "b.code",
    "brand_name": "b.name_zh",
    "product_name": "ci.name",
    "series_name": "ci.series_name",
    "model_number": "ci.model_number",
    "official_product_id": "sl.external_product_id",
    "official_sku_id": "sl.external_sku_id",
    "item_variant_id": "iv.id",
    "variant_catalog_item_id": "iv.catalog_item_id",
    "variant_attributes": "iv.attributes",
    "variant_manufacturer_part_number": "iv.manufacturer_part_number",
    "variant_measure_type": "iv.measure_type",
    "variant_condition_code": "iv.condition_code",
    "variant_quantity_value": "iv.quantity_value",
    "variant_quantity_min": "iv.quantity_min",
    "variant_quantity_max": "iv.quantity_max",
    "variant_base_unit": "iv.base_unit",
    "variant_package_count": "iv.package_count",
    "match_listing_revision_id": "lm.listing_revision_id",
    "match_item_variant_id": "lm.item_variant_id",
    "match_status": "lm.match_status",
    "match_effective_from": "lm.effective_from",
    "match_effective_to": "lm.effective_to",
    "match_count": """(SELECT COUNT(*) FROM v2_listing_match AS other_match
        WHERE other_match.listing_revision_id = lr.id
          AND other_match.match_status = 'ACCEPTED'
          AND other_match.effective_from <= :match_at
          AND (other_match.effective_to IS NULL OR other_match.effective_to > :match_at))""",
}

COMMON_FROM = """
FROM v2_price_current AS pc
JOIN v2_source_listing AS sl ON sl.id = pc.source_listing_id
LEFT JOIN v2_listing_revision AS lr ON lr.id = pc.listing_revision_id
LEFT JOIN v2_price_observation AS po ON po.id = pc.price_observation_id
JOIN v2_source_channel AS sc ON sc.id = sl.source_channel_id
LEFT JOIN v2_merchant AS m ON m.id = sl.merchant_id
LEFT JOIN v2_crawl_record AS cr ON cr.id = po.crawl_record_id
LEFT JOIN v2_crawl_run AS run ON run.id = cr.crawl_run_id
"""

# Do not remove located candidates whose pointer, quality or evidence relation
# is contradictory: the row adapter reports contract_error, not no_match.
# Missing revisions that cannot match a subject are outside this bounded read;
# this is deliberately not a database-wide orphan/integrity scan.
COMMON_WHERE = """
WHERE 1 = 1
"""

DEVICE_FROM = """
JOIN v2_listing_match AS lm ON lm.listing_revision_id = lr.id
JOIN v2_item_variant AS iv ON iv.id = lm.item_variant_id
JOIN v2_catalog_item AS ci ON ci.id = iv.catalog_item_id
JOIN v2_brand AS b ON b.id = ci.brand_id
JOIN v2_category AS cat ON cat.id = ci.category_id
"""

DEVICE_WHERE = """
  AND lm.match_status = 'ACCEPTED' AND lm.effective_from <= :match_at
  AND (lm.effective_to IS NULL OR lm.effective_to > :match_at)
  AND b.code IN ('APPLE', 'HUAWEI', 'XIAOMI', 'OPPO', 'VIVO')
  AND cat.code IN ('PHONE', 'TABLET', 'LAPTOP', 'DESKTOP', 'WATCH')
  AND sc.code = CONCAT(b.code, '_CN_WEB')
"""

FRESH_WHERE = """
  AND sc.code IN ('SH_FGW_FRESH_RETAIL', 'MOFCOM_FRESH_WHOLESALE')
"""


def _select(projection: dict[str, str]) -> str:
    return "SELECT\n" + ",\n".join(f"    {expr} AS {alias}" for alias, expr in projection.items())


DEVICE_SQL = _select(COMMON_PROJECTION | DEVICE_PROJECTION) + COMMON_FROM + DEVICE_FROM + COMMON_WHERE + DEVICE_WHERE
FRESH_SQL = _select(COMMON_PROJECTION) + COMMON_FROM + COMMON_WHERE + FRESH_WHERE


def _like(term: str) -> str:
    # Use an explicit non-backslash escape, independent of MySQL SQL_MODE.
    return "%" + term.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


def _term_filter(columns: tuple[str, ...], terms: tuple[str, ...]) -> tuple[str, dict[str, Any]]:
    clauses = []
    parameters = {}
    for index, term in enumerate(terms):
        name = f"term_{index}"
        parameters[name] = _like(term)
        clauses.extend(f"LOWER(COALESCE({column}, '')) LIKE :{name} ESCAPE '!'" for column in columns)
    return "\n AND (" + " OR ".join(clauses) + ")", parameters


class MySQLProductPriceRepository:
    """One pool, one bounded executor, one consistent snapshot per search.

    Cancelling an asyncio waiter cannot kill a DBAPI thread. Its semaphore permit
    stays held until the actual worker finishes; a stop event prevents subsequent
    statements. Server execution and socket timeouts bound in-flight work.
    """

    def __init__(self, *, dsn: str, pool_size: int, connect_timeout_seconds: float,
                 query_timeout_seconds: float) -> None:
        if type(pool_size) is not int or pool_size < 1 or any(
            isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or value <= 0
            for value in (connect_timeout_seconds, query_timeout_seconds)
        ):
            raise ValueError("repository limits must be finite and positive")
        try:
            if make_url(dsn).drivername != "mysql+pymysql":
                raise ValueError("unsupported price database driver")
        except (ValueError, SQLAlchemyError):
            raise ValueError("price database requires the mysql+pymysql driver") from None
        self._dsn = dsn
        self._pool_size = pool_size
        self._connect_timeout_seconds = connect_timeout_seconds
        self._query_timeout_seconds = query_timeout_seconds
        self._engine: Engine | None = None
        self._ready = False
        self._contract_error = False
        self._closed = False
        self._initialize_lock = asyncio.Lock()
        self._engine_lock = threading.Lock()
        self._query_capacity = asyncio.Semaphore(pool_size)
        self._executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="product-price")
        self._workers: set[asyncio.Future[Any]] = set()
        self._stop_events: set[threading.Event] = set()
        self._close_task: asyncio.Task[None] | None = None

    def _build_engine(self) -> Engine:
        engine = create_engine(
            self._dsn, pool_pre_ping=True, pool_recycle=1800,
            pool_size=self._pool_size, max_overflow=0,
            pool_timeout=self._query_timeout_seconds,
            isolation_level="REPEATABLE READ",
            connect_args={
                "connect_timeout": max(1, math.ceil(self._connect_timeout_seconds)),
                "read_timeout": self._query_timeout_seconds,
                "write_timeout": self._query_timeout_seconds,
            },
            hide_parameters=True,
        )

        @event.listens_for(engine, "connect")
        def configure_session(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("SET time_zone = '+00:00'")
                cursor.execute("SET SESSION MAX_EXECUTION_TIME = %s", (
                    max(1, math.ceil(self._query_timeout_seconds * 1000)),
                ))
            finally:
                cursor.close()

        return engine

    async def _run(self, operation: Callable[[threading.Event, float], T], *, deadline: float) -> T:
        stop = threading.Event()
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                await self._query_capacity.acquire()
                if self._closed:
                    self._query_capacity.release()
                    raise PriceRepositoryUnavailableError("价格数据连接已关闭")
                try:
                    future = asyncio.get_running_loop().run_in_executor(
                        self._executor, operation, stop, deadline,
                    )
                except BaseException:
                    self._query_capacity.release()
                    raise
                self._workers.add(future)
                self._stop_events.add(stop)

                def finished(done: asyncio.Future[Any]) -> None:
                    self._workers.discard(done)
                    self._stop_events.discard(stop)
                    self._query_capacity.release()
                    # Retrieve exceptions even when the requesting task is gone.
                    if not done.cancelled():
                        done.exception()

                future.add_done_callback(finished)
                return await asyncio.shield(future)
        except TimeoutError:
            stop.set()
            raise ProductPriceTimeoutError("价格查询超时") from None
        except asyncio.CancelledError:
            stop.set()
            raise

    @staticmethod
    def _check_deadline(stop: threading.Event, deadline: float) -> None:
        if stop.is_set() or time.monotonic() >= deadline:
            raise ProductPriceTimeoutError("价格查询超时")

    @classmethod
    def _execute(cls, connection: Any, sql: str, parameters: dict[str, Any],
                 stop: threading.Event, deadline: float) -> list[Any]:
        cls._check_deadline(stop, deadline)
        rows = connection.execute(text(sql), parameters).mappings().all()
        cls._check_deadline(stop, deadline)
        return rows

    def _initialize_sync(self, stop: threading.Event, deadline: float) -> None:
        self._check_deadline(stop, deadline)
        with self._engine_lock:
            self._check_deadline(stop, deadline)
            if self._engine is None:
                self._engine = self._build_engine()
        with self._engine.connect() as connection, connection.begin():
            self._execute(connection, "SELECT 1", {}, stop, deadline)
            # Compile and authorize every required projection, including device
            # tables on an otherwise empty fresh-only database. No row counts.
            self._execute(connection, DEVICE_SQL + "\nLIMIT 0", {
                "match_at": datetime.now(timezone.utc).replace(tzinfo=None),
            }, stop, deadline)
            self._execute(connection, FRESH_SQL + "\nLIMIT 0", {}, stop, deadline)

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._ready or self._closed:
                return
            self._contract_error = False
            try:
                await self._run(self._initialize_sync, deadline=time.monotonic()
                                + self._connect_timeout_seconds + self._query_timeout_seconds)
                if not self._closed:
                    self._ready, self._contract_error = True, False
            except ProductPriceContractError:
                self._ready, self._contract_error = False, True
            except DBAPIError as error:
                self._ready = False
                self._contract_error = self._error_code(error) in {1054, 1146}
            except (SQLAlchemyError, PriceRepositoryError, ValueError):
                self._ready = False

    @staticmethod
    def _error_code(error: DBAPIError) -> int | None:
        args = getattr(error.orig, "args", ())
        return args[0] if args and isinstance(args[0], int) else None

    def _search_sync(self, query: ProductPriceReadQuery, stop: threading.Event,
                     deadline: float) -> PriceReadBatch:
        if self._engine is None:
            raise PriceRepositoryUnavailableError("价格数据库尚未初始化")
        rows: list[Any] = []
        truncated = False
        with self._engine.connect() as connection, connection.begin():
            if isinstance(query, DevicePriceReadQuery):
                filters, parameters = _term_filter(("ci.name", "ci.series_name", "ci.model_number"), query.terms)
                parameters["match_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
                if query.brand_code is not None:
                    filters += "\n AND b.code = :brand_code"
                    parameters["brand_code"] = query.brand_code
                if query.category_code is not None:
                    filters += "\n AND cat.code = :category_code"
                    parameters["category_code"] = query.category_code
                products = self._execute(connection,
                    "SELECT DISTINCT ci.id AS catalog_item_id" + COMMON_FROM + DEVICE_FROM
                    + COMMON_WHERE + DEVICE_WHERE + filters
                    + "\nORDER BY ci.id LIMIT :product_limit",
                    parameters | {"product_limit": query.product_limit + 1}, stop, deadline)
                truncated = len(products) > query.product_limit
                for product in products[:query.product_limit]:
                    product_rows = self._execute(connection, DEVICE_SQL
                        + "\n AND ci.id = :catalog_item_id"
                        + "\nORDER BY iv.id, sl.id, pc.region_scope, pc.region_code, po.id"
                        + "\nLIMIT :per_product_limit",
                        parameters | {"catalog_item_id": product["catalog_item_id"],
                                      "per_product_limit": query.per_product_limit + 1}, stop, deadline)
                    truncated |= len(product_rows) > query.per_product_limit
                    rows.extend(product_rows[:query.per_product_limit])
            else:
                filters, parameters = _term_filter((
                    "JSON_UNQUOTE(JSON_EXTRACT(lr.normalized_attributes, '$.commodity_name'))",
                ), query.terms)
                if query.commodity_code is not None:
                    filters += "\n AND JSON_UNQUOTE(JSON_EXTRACT(lr.normalized_attributes, '$.commodity_code')) = :commodity_code"
                    parameters["commodity_code"] = query.commodity_code
                if query.region is not None:
                    filters += "\n AND pc.region_scope = :region_scope AND pc.region_code = :region_code"
                    parameters |= {"region_scope": query.region.scope, "region_code": query.region.code}
                if query.price_nature is not None:
                    filters += "\n AND po.price_nature = :price_nature"
                    parameters["price_nature"] = query.price_nature
                if query.market_id is not None:
                    filters += "\n AND JSON_UNQUOTE(JSON_EXTRACT(lr.source_attributes, '$.source_market_id')) = :market_id"
                    parameters["market_id"] = query.market_id
                rows = self._execute(connection, FRESH_SQL + filters
                    + "\nORDER BY sc.code, sl.id, pc.region_scope, pc.region_code, po.id"
                    + "\nLIMIT :listing_limit", parameters | {"listing_limit": query.listing_limit + 1},
                    stop, deadline)
                truncated = len(rows) > query.listing_limit
                rows = rows[:query.listing_limit]
        self._check_deadline(stop, deadline)
        read_at = datetime.now(timezone.utc)
        records = tuple(to_product_price_record(row, kind=query.kind, read_at=read_at) for row in rows)
        self._check_deadline(stop, deadline)
        try:
            return PriceReadBatch(records=records, truncated=truncated)
        except ValidationError:
            raise ProductPriceContractError("价格数据不符合消费合同") from None

    async def search(self, query: ProductPriceReadQuery) -> PriceReadBatch:
        try:
            # Defend even direct Repository callers; a service wrapper is not a
            # resource-boundary guarantee (nor is an unvalidated model_construct).
            query = PRODUCT_PRICE_READ_QUERY.validate_python(
                query.model_dump() if hasattr(query, "model_dump") else query,
            )
        except ValidationError:
            raise ProductPriceContractError("价格查询条件无效") from None
        deadline = time.monotonic() + self._query_timeout_seconds
        try:
            async with asyncio.timeout(self._query_timeout_seconds):
                if not self._ready:
                    await self.initialize()
                if self._contract_error:
                    raise ProductPriceContractError("价格数据库结构不符合消费合同")
                if not self._ready or self._closed:
                    raise PriceRepositoryUnavailableError("价格数据库尚未就绪")
                return await self._run(lambda stop, end: self._search_sync(query, stop, end), deadline=deadline)
        except TimeoutError:
            raise ProductPriceTimeoutError("价格查询超时") from None
        except DBAPIError as error:
            code = self._error_code(error)
            if code in {3024, 1969, 1205, 2013}:
                raise ProductPriceTimeoutError("价格查询超时") from None
            self._ready = False
            if code in {1054, 1146}:
                self._contract_error = True
                raise ProductPriceContractError("价格数据库结构不符合消费合同") from None
            raise PriceRepositoryUnavailableError("价格查询服务暂不可用") from None
        except SQLAlchemyError:
            self._ready = False
            raise PriceRepositoryUnavailableError("价格查询服务暂不可用") from None

    def readiness(self) -> str:
        if self._closed:
            return "not_ready"
        if self._contract_error:
            return "contract_error"
        return "ready" if self._ready and not self._closed else "not_ready"

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        # A cancelled lifecycle waiter must not abandon cleanup. Concurrent
        # close calls observe the same cleanup task and dispose exactly once.
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        self._closed, self._ready = True, False
        for stop in tuple(self._stop_events):
            stop.set()
        if self._workers:
            await asyncio.shield(asyncio.gather(*tuple(self._workers), return_exceptions=True))
        engine, self._engine = self._engine, None
        if engine is not None:
            await asyncio.to_thread(engine.dispose)
        self._executor.shutdown(wait=True, cancel_futures=True)
