from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import uvicorn

from .adapters.fake_shipping import (
    FakeDeliveryTimeGateway,
    FakePostageGateway,
)
from .adapters.fake_tracking import FakeTrackingGateway
from .api.agent_contracts import AgentApiDependencies
from .api.app import create_app
from .domain.device_price import DevicePriceRecord, DevicePriceSearchQuery
from .domain.models import QueryMode
from .domain.policy import PolicyCitation, PolicyQueryResult
from .domain.results import (
    DeliveryTimeData,
    PostageData,
    TrackingData,
    TrackingEvent,
)
from .domain.slots import RegionRef, RegionResolution, WeightValue
from .observability.logging import configure_logging
from .settings import AssistantSettings
from .tools.device_price import DevicePriceTool
from .tools.policy import PolicyKnowledgeTool
from .workflow.composition import create_persistent_agent


DEMO_MAIL_NO = "1234567890123"


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


class _DemoPolicySource:
    def __init__(self) -> None:
        self._ready = False

    async def initialize(self) -> None:
        self._ready = True

    async def query(self, question: str) -> PolicyQueryResult:
        del question
        return PolicyQueryResult(
            answer="演示政策资料建议准备可证明寄递事实和损失的材料[1]。",
            citations=(
                PolicyCitation(
                    index=1,
                    chunk_id="demo-policy-chunk-1",
                    document_id="demo-policy-document-1",
                    title="理赔材料演示政策",
                    source_url="https://example.test/policy/demo",
                    document_no="演示文号〔2026〕1号",
                    published_at="2026-01-01",
                    source_org="演示政策来源",
                    section_path="第二章/材料要求",
                    score=0.91,
                    rerank_score=0.94,
                    excerpt="申请人应提供能够证明寄递事实和实际损失的材料。",
                ),
            ),
            finish_reason="stop",
            usage={},
        )

    def readiness(self) -> str:
        return "ready" if self._ready else "not_ready"

    async def close(self) -> None:
        self._ready = False


class _DemoPriceRepository:
    def __init__(self) -> None:
        self._ready = False
        self._records = [
            DevicePriceRecord(
                offer_id=1,
                brand_code="APPLE",
                brand_name="Apple",
                official_product_id="demo-iphone-16-pro",
                product_name="iPhone 16 Pro",
                series_name="iPhone 16",
                model_number="A0001",
                official_product_url=(
                    "https://example.test/device/iphone-16-pro"
                ),
                official_sku_id="demo-iphone-16-pro-256gb",
                sku_name="iPhone 16 Pro 256GB 黑色",
                color="黑色",
                capacity="256GB",
                memory="",
                connectivity="5G",
                size="6.3 英寸",
                availability="ON_SALE",
                source_url=(
                    "https://example.test/device/iphone-16-pro/256gb"
                ),
                channel_name="演示官方商城",
                currency="CNY",
                original_price=Decimal("8999.00"),
                original_price_type="LIST_PRICE",
                current_price=Decimal("7999.00"),
                observed_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        ]

    async def initialize(self) -> None:
        self._ready = True

    async def search(
        self,
        query: DevicePriceSearchQuery,
    ) -> list[DevicePriceRecord]:
        del query
        return list(self._records)

    def readiness(self) -> str:
        return "ready" if self._ready else "not_ready"

    async def close(self) -> None:
        self._ready = False


def _demo_database_path() -> Path:
    configured = os.getenv("ASSISTANT_AGENT_DEMO_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(tempfile.gettempdir()) / "spb-assistant-agent-demo.db"


def create_demo_app(*, database_path: str | Path | None = None):
    """Create a network-free five-capability V2 demonstration."""

    resolved_database = Path(database_path or _demo_database_path())
    policy_tool = PolicyKnowledgeTool(source=_DemoPolicySource())
    device_price_tool = DevicePriceTool(
        repository=_DemoPriceRepository(),
        candidate_limit=20,
        result_limit=5,
        match_threshold=65,
    )
    legacy_tools = {
        QueryMode.POLICY: policy_tool,
        QueryMode.DEVICE_PRICE: device_price_tool,
    }

    @asynccontextmanager
    async def agent_dependencies():
        now = datetime.now(UTC)
        tracking = TrackingData(
            mail_no=DEMO_MAIL_NO,
            current_status="运输中",
            queried_at=now,
            events=[
                TrackingEvent(
                    event_code="ACCEPTED",
                    description="邮件已收寄",
                    occurred_at=now,
                    location="北京市",
                ),
                TrackingEvent(
                    event_code="IN_TRANSIT",
                    description="邮件正在发往下一处理中心",
                    occurred_at=now,
                    location="北京市",
                ),
            ],
        )
        delivery = DeliveryTimeData(
            origin=BEIJING,
            destination=SHANGHAI,
            estimated_duration=Decimal("2"),
            duration_unit="天",
            service_level="标准快递",
            estimate_basis="Phase 4D 本地演示 fixture",
            queried_at=now,
        )
        postage = PostageData(
            origin=BEIJING,
            destination=SHANGHAI,
            input_weight=WeightValue(value=Decimal("2"), unit="kg"),
            billable_weight=WeightValue(value=Decimal("2"), unit="kg"),
            amount=Decimal("12.30"),
            currency="CNY",
            product_code="DEMO_STANDARD",
            queried_at=now,
        )
        async with create_persistent_agent(
            database_path=resolved_database,
            tracking_gateway=FakeTrackingGateway({DEMO_MAIL_NO: tracking}),
            delivery_time_gateway=FakeDeliveryTimeGateway(delivery),
            postage_gateway=FakePostageGateway(postage),
            policy_tool=policy_tool,
            device_price_tool=device_price_tool,
        ) as components:
            yield AgentApiDependencies(
                service=components.service,
                capabilities=components.runtime.capability_descriptors,
                readiness_probe=components.readiness,
                janitor=components.janitor,
            )

    settings = AssistantSettings(
        auth_enabled=False,
        rate_limit_enabled=False,
        metrics_enabled=True,
    )
    return create_app(
        settings=settings,
        tools=legacy_tools,
        agent_api_factory=agent_dependencies,
    )


app = create_demo_app()


def run() -> None:
    settings = AssistantSettings()
    configure_logging(
        level=settings.log_level,
        json_output=settings.log_json,
    )
    uvicorn.run(
        "spb_assistant_api.agent_demo:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=settings.log_level,
        log_config=None,
        access_log=False,
    )
