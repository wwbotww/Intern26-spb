from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from spb_assistant_api.domain.device_price import (
    DevicePriceRecord,
    DevicePriceSearchQuery,
)
from spb_assistant_api.domain.exceptions import (
    PriceRepositoryUnavailableError,
    ToolUnavailableError,
)
from spb_assistant_api.domain.models import ToolStatus
from spb_assistant_api.tools.device_price import DevicePriceTool


class FakePriceRepository:
    def __init__(
        self,
        records: list[DevicePriceRecord] | None = None,
        *,
        unavailable: bool = False,
    ) -> None:
        self.records = records or []
        self.unavailable = unavailable
        self.queries: list[DevicePriceSearchQuery] = []
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def search(
        self,
        query: DevicePriceSearchQuery,
    ) -> list[DevicePriceRecord]:
        self.queries.append(query)
        if self.unavailable:
            raise PriceRepositoryUnavailableError("not ready")
        return self.records

    def readiness(self) -> str:
        return "ready" if self.initialized else "not_ready"

    async def close(self) -> None:
        self.closed = True


def _record(
    *,
    offer_id: int,
    capacity: str,
    current_price: str,
    availability: str = "ON_SALE",
) -> DevicePriceRecord:
    return DevicePriceRecord(
        offer_id=offer_id,
        brand_code="APPLE",
        brand_name="Apple",
        official_product_id="iphone-16-pro",
        product_name="iPhone 16 Pro",
        series_name="iPhone 16",
        model_number="A0001",
        official_product_url="https://example.test/product",
        official_sku_id=f"sku-{offer_id}",
        sku_name=f"iPhone 16 Pro {capacity}",
        color="黑色",
        capacity=capacity,
        memory="",
        connectivity="5G",
        size="6.3 英寸",
        availability=availability,
        source_url=f"https://example.test/offer/{offer_id}",
        channel_name="官方商城",
        currency="CNY",
        original_price=Decimal("8999.00"),
        original_price_type="LIST_PRICE",
        current_price=Decimal(current_price),
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def _tool(repository: FakePriceRepository) -> DevicePriceTool:
    return DevicePriceTool(
        repository=repository,
        candidate_limit=500,
        result_limit=20,
        match_threshold=65,
    )


def test_device_price_tool_returns_multiple_candidates_with_evidence() -> None:
    repository = FakePriceRepository(
        [
            _record(
                offer_id=1,
                capacity="256GB",
                current_price="7999.00",
            ),
            _record(
                offer_id=2,
                capacity="512GB",
                current_price="9999.00",
            ),
        ]
    )
    tool = _tool(repository)

    asyncio.run(tool.initialize())
    result = asyncio.run(tool.execute("iPhone 16 Pro 多少钱"))

    assert result.status is ToolStatus.SUCCESS
    assert len(result.evidence) == 2
    assert result.evidence[0].price == "7999.00"
    assert result.evidence[0].original_price == "8999.00"
    assert result.evidence[0].official_sku_id == "sku-1"
    assert result.evidence[0].match_score >= 65
    assert "多个 SKU" in result.warnings[0]
    assert repository.queries[0].brand_code == "APPLE"
    assert repository.queries[0].limit == 500


def test_device_price_tool_filters_by_requested_capacity() -> None:
    repository = FakePriceRepository(
        [
            _record(
                offer_id=1,
                capacity="256GB",
                current_price="7999.00",
            ),
            _record(
                offer_id=2,
                capacity="512GB",
                current_price="9999.00",
            ),
        ]
    )

    result = asyncio.run(
        _tool(repository).execute("iPhone 16 Pro 512G 多少钱")
    )

    assert result.status is ToolStatus.SUCCESS
    assert len(result.evidence) == 1
    assert result.evidence[0].specification.startswith("512GB")
    assert result.evidence[0].price == "9999.00"


@pytest.mark.parametrize(
    "question",
    ["这个型号呢？", "这个设备多少钱？", "这款手机什么价格？"],
)
def test_device_price_tool_does_not_query_when_model_is_missing(
    question: str,
) -> None:
    repository = FakePriceRepository()

    result = asyncio.run(_tool(repository).execute(question))

    assert result.status is ToolStatus.NEED_MORE_INFO
    assert result.missing_fields == ("brand_or_model",)
    assert repository.queries == []


def test_device_price_tool_blocks_cross_category_request() -> None:
    repository = FakePriceRepository()

    result = asyncio.run(
        _tool(repository).execute(
            "iPhone 16 Pro 多少钱，同时理赔要准备什么材料？"
        )
    )

    assert result.status is ToolStatus.NEED_MORE_INFO
    assert result.reason_code == "multiple_query_categories"
    assert repository.queries == []


def test_device_price_tool_distinguishes_no_model_and_no_spec_match() -> None:
    no_model_repository = FakePriceRepository()
    no_model = asyncio.run(
        _tool(no_model_repository).execute("iPhone 16 Pro 多少钱")
    )
    spec_repository = FakePriceRepository(
        [
            _record(
                offer_id=1,
                capacity="256GB",
                current_price="7999.00",
            )
        ]
    )
    no_spec = asyncio.run(
        _tool(spec_repository).execute("iPhone 16 Pro 1TB 多少钱")
    )

    assert no_model.status is ToolStatus.NO_MATCH
    assert no_spec.status is ToolStatus.NO_MATCH
    assert no_spec.missing_fields == ("matching_specification",)


def test_device_price_tool_surfaces_repository_unavailability() -> None:
    repository = FakePriceRepository(unavailable=True)

    with pytest.raises(ToolUnavailableError):
        asyncio.run(
            _tool(repository).execute("iPhone 16 Pro 多少钱")
        )
