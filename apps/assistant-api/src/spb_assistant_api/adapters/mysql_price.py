from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError

from ..domain.device_price import (
    DevicePriceRecord,
    DevicePriceSearchQuery,
)
from ..domain.exceptions import (
    PriceRepositoryError,
    PriceRepositoryUnavailableError,
)


logger = logging.getLogger(__name__)

SEARCH_COLUMNS = (
    "p.name",
    "p.series_name",
    "p.model_number",
    "p.official_product_id",
    "s.name",
    "s.official_sku_id",
    "s.capacity",
    "s.memory",
    "s.connectivity",
    "s.size",
)

BASE_QUERY = """
SELECT
    o.id AS offer_id,
    b.code AS brand_code,
    b.name_zh AS brand_name,
    p.official_product_id,
    p.name AS product_name,
    COALESCE(p.series_name, '') AS series_name,
    COALESCE(p.model_number, '') AS model_number,
    p.official_url AS official_product_url,
    COALESCE(s.official_sku_id, '') AS official_sku_id,
    s.name AS sku_name,
    COALESCE(s.color, '') AS color,
    COALESCE(s.capacity, '') AS capacity,
    COALESCE(s.memory, '') AS memory,
    COALESCE(s.connectivity, '') AS connectivity,
    COALESCE(s.size, '') AS size,
    o.availability,
    o.source_url,
    sc.name AS channel_name,
    pc.currency,
    pc.original_price,
    pc.original_price_type,
    pc.current_price,
    pc.observed_at
FROM price_current AS pc
JOIN official_offer AS o ON o.id = pc.offer_id
JOIN sku AS s ON s.id = o.sku_id
JOIN product AS p ON p.id = s.product_id
JOIN brand AS b ON b.id = p.brand_id
JOIN sales_channel AS sc ON sc.id = o.channel_id
WHERE pc.current_price IS NOT NULL
"""


class MySQLPriceRepository:
    """Fixed read-only projection over the device-price schema."""

    def __init__(
        self,
        *,
        dsn: str,
        pool_size: int,
        connect_timeout_seconds: float,
        query_timeout_seconds: float,
    ) -> None:
        self._dsn = dsn
        self._pool_size = pool_size
        self._connect_timeout_seconds = connect_timeout_seconds
        self._query_timeout_seconds = query_timeout_seconds
        self._engine: Engine | None = None
        self._ready = False
        self._initialize_lock = asyncio.Lock()
        self._query_capacity = asyncio.Semaphore(pool_size)

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._ready:
                return
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._initialize_sync),
                    timeout=self._connect_timeout_seconds + 1,
                )
            except Exception:
                self._ready = False
                logger.exception("device price database initialization failed")

    def _initialize_sync(self) -> None:
        if self._engine is None:
            self._engine = self._build_engine()
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        self._ready = True

    def _build_engine(self) -> Engine:
        timeout = max(1, int(self._connect_timeout_seconds))
        query_timeout = max(1, int(self._query_timeout_seconds))
        engine = create_engine(
            self._dsn,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=self._pool_size,
            max_overflow=0,
            connect_args={
                "connect_timeout": timeout,
                "read_timeout": query_timeout,
                "write_timeout": timeout,
            },
        )

        @event.listens_for(engine, "connect")
        def configure_read_only_session(
            dbapi_connection: Any,
            connection_record: Any,
        ) -> None:
            del connection_record
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("SET time_zone = '+00:00'")
            finally:
                cursor.close()

        return engine

    async def search(
        self,
        query: DevicePriceSearchQuery,
    ) -> list[DevicePriceRecord]:
        if not self._ready:
            await self.initialize()
        if not self._ready or self._engine is None:
            raise PriceRepositoryUnavailableError(
                "设备价格数据库尚未就绪"
            )

        async with self._query_capacity:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._search_sync, query),
                    timeout=self._query_timeout_seconds,
                )
            except TimeoutError as exc:
                raise PriceRepositoryError("设备价格查询超时") from exc
            except SQLAlchemyError as exc:
                self._ready = False
                logger.exception("device price query failed")
                raise PriceRepositoryError("设备价格查询失败") from exc

    def _search_sync(
        self,
        query: DevicePriceSearchQuery,
    ) -> list[DevicePriceRecord]:
        if self._engine is None:
            raise PriceRepositoryUnavailableError(
                "设备价格数据库尚未初始化"
            )

        sql = BASE_QUERY
        parameters: dict[str, Any] = {}
        if query.brand_code:
            sql += "\nAND b.code = :brand_code"
            parameters["brand_code"] = query.brand_code
        if query.terms:
            clauses: list[str] = []
            for index, term in enumerate(query.terms):
                key = f"term_{index}"
                parameters[key] = f"%{term}%"
                clauses.extend(
                    f"LOWER(COALESCE({column}, '')) LIKE :{key}"
                    for column in SEARCH_COLUMNS
                )
            sql += "\nAND (" + " OR ".join(clauses) + ")"
        sql += """
ORDER BY
    CASE o.availability
        WHEN 'ON_SALE' THEN 0
        WHEN 'RESERVATION' THEN 1
        WHEN 'PRE_SALE' THEN 2
        WHEN 'OUT_OF_STOCK' THEN 3
        ELSE 4
    END,
    pc.observed_at DESC,
    p.id,
    s.id
LIMIT :candidate_limit
"""
        parameters["candidate_limit"] = query.limit

        with self._engine.connect() as connection:
            rows = connection.execute(text(sql), parameters).mappings().all()
        return [self._to_record(row) for row in rows]

    @staticmethod
    def _to_record(row: Mapping[str, Any]) -> DevicePriceRecord:
        current_price = row["current_price"]
        if not isinstance(current_price, Decimal):
            current_price = Decimal(str(current_price))
        original_price = row["original_price"]
        if original_price is not None and not isinstance(
            original_price,
            Decimal,
        ):
            original_price = Decimal(str(original_price))
        observed_at = row["observed_at"]
        if not isinstance(observed_at, datetime):
            raise PriceRepositoryError("价格观察时间字段无效")

        def string(name: str) -> str:
            value = row[name]
            return "" if value is None else str(value)

        return DevicePriceRecord(
            offer_id=int(row["offer_id"]),
            brand_code=string("brand_code"),
            brand_name=string("brand_name"),
            official_product_id=string("official_product_id"),
            product_name=string("product_name"),
            series_name=string("series_name"),
            model_number=string("model_number"),
            official_product_url=string("official_product_url"),
            official_sku_id=string("official_sku_id"),
            sku_name=string("sku_name"),
            color=string("color"),
            capacity=string("capacity"),
            memory=string("memory"),
            connectivity=string("connectivity"),
            size=string("size"),
            availability=string("availability"),
            source_url=string("source_url"),
            channel_name=string("channel_name"),
            currency=string("currency"),
            original_price=original_price,
            original_price_type=string("original_price_type"),
            current_price=current_price,
            observed_at=observed_at,
        )

    def readiness(self) -> str:
        return "ready" if self._ready else "not_ready"

    async def close(self) -> None:
        engine = self._engine
        self._engine = None
        self._ready = False
        if engine is not None:
            await asyncio.to_thread(engine.dispose)
