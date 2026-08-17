from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from spb_assistant_api.adapters.mysql_price import MySQLPriceRepository
from spb_assistant_api.domain.device_price import DevicePriceSearchQuery


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.sql = ""
        self.parameters: dict[str, Any] = {}

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def execute(
        self,
        statement: object,
        parameters: dict[str, Any],
    ) -> FakeResult:
        self.sql = str(statement)
        self.parameters = parameters
        return FakeResult(self._rows)


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def connect(self) -> FakeConnection:
        return self.connection

    def dispose(self) -> None:
        return None


def _row() -> dict[str, Any]:
    return {
        "offer_id": 1,
        "brand_code": "APPLE",
        "brand_name": "Apple",
        "official_product_id": "product-1",
        "product_name": "iPhone 16 Pro",
        "series_name": "iPhone 16",
        "model_number": "A0001",
        "official_product_url": "https://example.test/product",
        "official_sku_id": "sku-1",
        "sku_name": "iPhone 16 Pro 256GB",
        "color": "黑色",
        "capacity": "256GB",
        "memory": "",
        "connectivity": "5G",
        "size": "6.3 英寸",
        "availability": "ON_SALE",
        "source_url": "https://example.test/offer",
        "channel_name": "官方商城",
        "currency": "CNY",
        "original_price": Decimal("8999.00"),
        "original_price_type": "LIST_PRICE",
        "current_price": Decimal("7999.00"),
        "observed_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
    }


def test_search_uses_fixed_sql_and_bound_user_values() -> None:
    connection = FakeConnection([_row()])
    repository = MySQLPriceRepository(
        dsn="mysql+pymysql://unused",
        pool_size=1,
        connect_timeout_seconds=1,
        query_timeout_seconds=1,
    )
    repository._engine = FakeEngine(connection)  # type: ignore[assignment]
    repository._ready = True
    hostile_term = "16 pro' OR 1=1"

    records = asyncio.run(
        repository.search(
            DevicePriceSearchQuery(
                brand_code="APPLE",
                terms=(hostile_term,),
                limit=25,
            )
        )
    )

    assert len(records) == 1
    assert records[0].current_price == Decimal("7999.00")
    assert "b.code = :brand_code" in connection.sql
    assert "LIKE :term_0" in connection.sql
    assert hostile_term not in connection.sql
    assert connection.parameters == {
        "brand_code": "APPLE",
        "term_0": f"%{hostile_term}%",
        "candidate_limit": 25,
    }
