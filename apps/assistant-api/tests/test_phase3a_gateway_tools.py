from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from spb_assistant_api.adapters.checkpointer_factory import (
    create_in_memory_checkpointer,
)
from spb_assistant_api.adapters.fake_shipping import (
    FakeDeliveryTimeGateway,
    FakePostageGateway,
)
from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.adapters.in_memory_receipts import (
    InMemoryToolExecutionRepository,
)
from spb_assistant_api.domain.failures import AgentFailure, FailureCategory
from spb_assistant_api.domain.results import DeliveryTimeData, PostageData
from spb_assistant_api.domain.slots import (
    RegionRef,
    RegionResolution,
    WeightValue,
)
from spb_assistant_api.workflow.composition import (
    create_agent_runtime,
)


NOW = datetime(2026, 9, 4, 8, tzinfo=UTC)


def _region(name: str, province_code: str, city_code: str) -> RegionRef:
    return RegionRef(
        raw_text=name,
        canonical_name=name,
        province_code=province_code,
        city_code=city_code,
        resolution=RegionResolution.RESOLVED,
    )


BEIJING = _region("北京市", "110000", "110100")
SHANGHAI = _region("上海市", "310000", "310100")
GUANGZHOU = _region("广州市", "440000", "440100")
WEIGHT = WeightValue(value=Decimal("2"), unit="kg")


def _runtime(
    *,
    delivery_data: DeliveryTimeData | None,
    postage_data: PostageData | None,
    delivery_failures: tuple[AgentFailure, ...] = (),
):
    delivery = FakeDeliveryTimeGateway(
        delivery_data,
        scripted_failures=delivery_failures,
    )
    postage = FakePostageGateway(postage_data)
    receipts = InMemoryToolExecutionRepository()
    runtime = create_agent_runtime(
        checkpointer=create_in_memory_checkpointer(),
        receipts=receipts,
        tracking_gateway=FakeTrackingGateway(),
        delivery_time_gateway=delivery,
        postage_gateway=postage,
        clock=lambda: NOW,
    )
    return runtime, delivery, postage, receipts


def test_graph_routes_delivery_time_and_postage_to_distinct_typed_tools() -> None:
    runtime, delivery, postage, receipts = _runtime(
        delivery_data=DeliveryTimeData(
            origin=BEIJING,
            destination=SHANGHAI,
            estimated_duration=Decimal("2"),
            duration_unit="day",
            queried_at=NOW,
        ),
        postage_data=PostageData(
            origin=BEIJING,
            destination=SHANGHAI,
            input_weight=WEIGHT,
            amount=Decimal("12.30"),
            currency="CNY",
            queried_at=NOW,
        ),
    )

    delivery_result = asyncio.run(
        runtime.start(
            thread_id="phase3a-delivery",
            message="从北京寄到上海要多久",
        )
    )
    postage_result = asyncio.run(
        runtime.start(
            thread_id="phase3a-postage",
            message="北京寄上海 2 公斤多少钱",
        )
    )

    assert delivery_result["phase"] == "completed"
    assert delivery_result["result"]["tool"] == "delivery_time"
    assert postage_result["phase"] == "completed"
    assert postage_result["result"]["tool"] == "postage"
    assert postage_result["result"]["data"]["amount"] == "12.30"
    assert len(delivery.commands) == 1
    assert len(postage.commands) == 1
    assert len(receipts) == 2


def test_gateway_none_is_business_no_match_not_technical_failure() -> None:
    runtime, delivery, postage, _ = _runtime(
        delivery_data=None,
        postage_data=None,
    )

    delivery_result = asyncio.run(
        runtime.start(
            thread_id="phase3a-delivery-none",
            message="从北京寄到上海要多久",
        )
    )
    postage_result = asyncio.run(
        runtime.start(
            thread_id="phase3a-postage-none",
            message="北京寄上海 2 公斤多少钱",
        )
    )

    assert delivery_result["result"]["status"] == "no_match"
    assert delivery_result["failure"] is None
    assert postage_result["result"]["status"] == "no_match"
    assert postage_result["failure"] is None
    assert len(delivery.commands) == 1
    assert len(postage.commands) == 1


def test_delivery_result_with_different_route_fails_closed() -> None:
    runtime, delivery, _, _ = _runtime(
        delivery_data=DeliveryTimeData(
            origin=GUANGZHOU,
            destination=SHANGHAI,
            estimated_duration=Decimal("2"),
            duration_unit="day",
            queried_at=NOW,
        ),
        postage_data=None,
    )

    result = asyncio.run(
        runtime.start(
            thread_id="phase3a-route-mismatch",
            message="从北京寄到上海要多久",
        )
    )

    assert result["phase"] == "failed"
    assert result["result"] is None
    assert result["failure"]["code"] == "delivery_time_origin_mismatch"
    assert len(delivery.commands) == 1


def test_postage_result_with_different_input_weight_fails_closed() -> None:
    runtime, _, postage, _ = _runtime(
        delivery_data=None,
        postage_data=PostageData(
            origin=BEIJING,
            destination=SHANGHAI,
            input_weight=WeightValue(value=Decimal("3"), unit="kg"),
            amount=Decimal("15.00"),
            currency="CNY",
            queried_at=NOW,
        ),
    )

    result = asyncio.run(
        runtime.start(
            thread_id="phase3a-weight-mismatch",
            message="北京寄上海 2 公斤多少钱",
        )
    )

    assert result["phase"] == "failed"
    assert result["result"] is None
    assert result["failure"]["code"] == "postage_input_weight_mismatch"
    assert len(postage.commands) == 1


def test_retry_after_beyond_local_budget_does_not_repeat_gateway_call() -> None:
    runtime, delivery, _, _ = _runtime(
        delivery_data=None,
        postage_data=None,
        delivery_failures=(
            AgentFailure(
                category=FailureCategory.UPSTREAM_RATE_LIMITED,
                code="delivery_rate_limited",
                message="fixture rate limit",
                retryable=True,
                retry_after_seconds=30,
            ),
        ),
    )

    result = asyncio.run(
        runtime.start(
            thread_id="phase3a-rate-limit-budget",
            message="从北京寄到上海要多久",
        )
    )

    assert result["phase"] == "failed"
    assert result["failure"]["category"] == "upstream_rate_limited"
    assert len(delivery.commands) == 1
