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
    brand_code: str = "APPLE",
    brand_name: str = "Apple",
    official_product_id: str = "iphone-16-pro",
    product_name: str = "iPhone 16 Pro",
    series_name: str = "iPhone 16",
    model_number: str = "A0001",
    sku_name: str | None = None,
    memory: str = "",
    size: str = "6.3 英寸",
) -> DevicePriceRecord:
    return DevicePriceRecord(
        offer_id=offer_id,
        brand_code=brand_code,
        brand_name=brand_name,
        official_product_id=official_product_id,
        product_name=product_name,
        series_name=series_name,
        model_number=model_number,
        official_product_url="https://example.test/product",
        official_sku_id=f"sku-{offer_id}",
        sku_name=sku_name or f"{product_name} {capacity}",
        color="黑色",
        capacity=capacity,
        memory=memory,
        connectivity="5G",
        size=size,
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


def test_product_identity_does_not_use_sku_memory_or_size() -> None:
    repository = FakePriceRepository(
        [
            _record(
                offer_id=1,
                capacity="256GB",
                current_price="5999.00",
                official_product_id="iphone-16",
                product_name="iPhone 16",
                series_name="iPhone 16",
                model_number="",
            ),
            _record(
                offer_id=2,
                capacity="1TB",
                current_price="18999.00",
                official_product_id="macbook-pro",
                product_name="MacBook Pro",
                series_name="MacBook Pro",
                model_number="",
                sku_name="MacBook Pro 16GB 16 英寸",
                memory="16GB",
                size="16 英寸",
            ),
        ]
    )

    result = asyncio.run(_tool(repository).execute("iPhone 16 多少钱"))

    assert result.status is ToolStatus.SUCCESS
    assert {item.official_product_id for item in result.evidence} == {
        "iphone-16"
    }


def test_only_best_scoring_product_group_is_returned() -> None:
    repository = FakePriceRepository(
        [
            _record(
                offer_id=3,
                capacity="512GB",
                current_price="14999.00",
                official_product_id="mac-studio",
                product_name="Mac Studio",
                series_name="Mac Studio",
                model_number="",
            ),
            _record(
                offer_id=4,
                capacity="512GB",
                current_price="12999.00",
                official_product_id="imac",
                product_name="iMac",
                series_name="iMac",
                model_number="",
            ),
        ]
    )

    result = asyncio.run(_tool(repository).execute("Mac Studio 多少钱"))

    assert result.status is ToolStatus.SUCCESS
    assert {item.official_product_id for item in result.evidence} == {
        "mac-studio"
    }


def test_plus_variant_is_distinct_from_base_pro_model() -> None:
    repository = FakePriceRepository(
        [
            _record(
                offer_id=5,
                capacity="512GB",
                current_price="6999.00",
                brand_code="HUAWEI",
                brand_name="华为",
                official_product_id="mate-70-pro",
                product_name="HUAWEI Mate 70 Pro",
                series_name="Mate 70",
                model_number="",
            ),
            _record(
                offer_id=6,
                capacity="512GB",
                current_price="7999.00",
                brand_code="HUAWEI",
                brand_name="华为",
                official_product_id="mate-70-pro-plus",
                product_name="HUAWEI Mate 70 Pro+",
                series_name="Mate 70",
                model_number="",
            ),
        ]
    )
    tool = _tool(repository)

    base = asyncio.run(tool.execute("Mate 70 Pro 多少钱"))
    plus = asyncio.run(tool.execute("Mate 70 Pro+ 多少钱"))

    assert {item.official_product_id for item in base.evidence} == {
        "mate-70-pro"
    }
    assert {item.official_product_id for item in plus.evidence} == {
        "mate-70-pro-plus"
    }


@pytest.mark.parametrize(
    ("question", "record"),
    [
        (
            "iPhone 16 Pro 多少钱",
            _record(
                offer_id=10,
                capacity="256GB",
                current_price="5999.00",
                official_product_id="iphone-16",
                product_name="iPhone 16",
                series_name="iPhone 16",
                model_number="",
            ),
        ),
        (
            "Pura 70 Pro 多少钱",
            _record(
                offer_id=11,
                capacity="512GB",
                current_price="6999.00",
                brand_code="HUAWEI",
                brand_name="华为",
                official_product_id="mate-70-pro",
                product_name="HUAWEI Mate 70 Pro",
                series_name="Mate 70",
                model_number="",
            ),
        ),
        (
            "OPPO Find X8 Pro 多少钱",
            _record(
                offer_id=12,
                capacity="512GB",
                current_price="4999.00",
                brand_code="OPPO",
                brand_name="OPPO",
                official_product_id="find-x9s-pro",
                product_name="OPPO Find X9s Pro",
                series_name="Find X9",
                model_number="",
            ),
        ),
        (
            "小米 15 Pro 多少钱",
            _record(
                offer_id=13,
                capacity="512GB",
                current_price="5999.00",
                brand_code="XIAOMI",
                brand_name="小米",
                official_product_id="xiaomi-17-pro-max",
                product_name="Xiaomi 17 Pro Max",
                series_name="Xiaomi 17",
                model_number="",
            ),
        ),
    ],
)
def test_similar_but_different_product_is_rejected(
    question: str,
    record: DevicePriceRecord,
) -> None:
    result = asyncio.run(
        _tool(FakePriceRepository([record])).execute(question)
    )

    assert result.status is ToolStatus.NO_MATCH
    assert result.evidence == ()


def test_product_match_then_filters_huawei_sku_capacity_and_memory() -> None:
    repository = FakePriceRepository(
        [
            _record(
                offer_id=20,
                capacity="256GB",
                memory="12GB",
                current_price="5999.00",
                brand_code="HUAWEI",
                brand_name="华为",
                official_product_id="mate-70-pro",
                product_name="HUAWEI Mate 70 Pro",
                series_name="Mate 70",
                model_number="",
            ),
            _record(
                offer_id=21,
                capacity="512GB",
                memory="12GB",
                current_price="6999.00",
                brand_code="HUAWEI",
                brand_name="华为",
                official_product_id="mate-70-pro",
                product_name="HUAWEI Mate 70 Pro",
                series_name="Mate 70",
                model_number="",
            ),
        ]
    )

    result = asyncio.run(
        _tool(repository).execute(
            "华为 Mate 70 Pro 12GB+512GB 多少钱"
        )
    )

    assert result.status is ToolStatus.SUCCESS
    assert len(result.evidence) == 1
    assert result.evidence[0].official_product_id == "mate-70-pro"
    assert result.evidence[0].specification.startswith("512GB / 12GB")


@pytest.mark.parametrize(
    "question",
    [
        "这个型号呢？",
        "这个设备多少钱？",
        "这款手机什么价格？",
        "256GB 的设备多少钱？",
        "Pro 版多少钱？",
    ],
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
