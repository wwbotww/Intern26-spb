from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from ..domain.device_price import (
    DevicePriceRecord,
    DevicePriceSearchQuery,
)
from ..domain.device_query import (
    extract_capacity_tokens,
    normalize_text,
    parse_device_query,
)
from ..domain.exceptions import (
    PriceRepositoryUnavailableError,
    ToolUnavailableError,
)
from ..domain.models import DevicePriceEvidence, ToolResult, ToolStatus
from ..domain.ports import DevicePriceRepository
from ..services.device_price_matching import (
    DeviceMatchCandidate,
    DevicePriceMatchingStrategy,
    RankedDeviceCandidate,
)
from .query_scope import cross_category_result, is_cross_category_question


DEVICE_PRICE_TOOL_NAME = "device_price"


class DevicePriceTool:
    def __init__(
        self,
        *,
        repository: DevicePriceRepository,
        candidate_limit: int,
        result_limit: int,
        match_threshold: float,
    ) -> None:
        self._repository = repository
        self._candidate_limit = candidate_limit
        self._result_limit = result_limit
        self._match_threshold = match_threshold
        self._matching = DevicePriceMatchingStrategy()

    @property
    def name(self) -> str:
        return DEVICE_PRICE_TOOL_NAME

    async def initialize(self) -> None:
        await self._repository.initialize()

    async def execute(self, question: str) -> ToolResult:
        if is_cross_category_question(question):
            return cross_category_result(self.name)
        parsed = parse_device_query(question)
        if not parsed.sufficient:
            return ToolResult(
                tool=self.name,
                status=ToolStatus.NEED_MORE_INFO,
                answer=(
                    "请在本次问题中提供完整的设备品牌和型号，"
                    "如需指定版本，请同时提供容量或内存规格。"
                ),
                missing_fields=("brand_or_model",),
            )

        try:
            records = await self._repository.search(
                DevicePriceSearchQuery(
                    brand_code=parsed.brand_code,
                    terms=parsed.terms,
                    limit=self._candidate_limit,
                )
            )
        except PriceRepositoryUnavailableError as exc:
            raise ToolUnavailableError(self.name) from exc

        match = self._matching.match(
            parsed,
            [self._matching_candidate(record) for record in records],
            match_threshold=self._match_threshold,
        )
        ranked = match.ranked
        if match.specification_mismatch:
            return ToolResult(
                tool=self.name,
                status=ToolStatus.NO_MATCH,
                answer=(
                    "找到了可能的设备型号，但没有找到与所给容量或内存"
                    "规格一致的价格记录。请核对完整规格后重新查询。"
                ),
                missing_fields=("matching_specification",),
            )
        if not ranked:
            return ToolResult(
                tool=self.name,
                status=ToolStatus.NO_MATCH,
                answer=(
                    "未在当前设备价格库中找到足够匹配的型号记录。"
                    "请核对品牌、完整型号和容量后重新查询。"
                ),
            )

        truncated = len(ranked) > self._result_limit
        selected = ranked[: self._result_limit]
        evidence = tuple(
            self._to_evidence(index, item)
            for index, item in enumerate(selected, start=1)
        )
        warnings: list[str] = []
        if len(evidence) > 1:
            warnings.append(
                "同一型号可能包含多个 SKU，以下价格均为候选参考记录。"
            )
        if truncated:
            warnings.append(
                f"匹配结果超过展示上限，仅返回前 {self._result_limit} 条。"
            )
        if any(
            item.record.availability not in {
                "ON_SALE",
                "RESERVATION",
                "PRE_SALE",
            }
            for item in selected
        ):
            warnings.append("部分候选当前不是在售状态，请结合状态和观察时间判断。")

        return ToolResult(
            tool=self.name,
            status=ToolStatus.SUCCESS,
            answer=(
                f"查询到 {len(evidence)} 条可能匹配的设备参考价格记录。"
                "价格来自已采集的官方商城数据，不代表最终定损或赔付金额。"
            ),
            evidence=evidence,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _matching_candidate(
        record: DevicePriceRecord,
    ) -> DeviceMatchCandidate[DevicePriceRecord]:
        identifier = record.official_product_id.strip()
        if not identifier:
            identifier = "|".join(
                normalize_text(value)
                for value in (
                    record.product_name, record.series_name, record.model_number
                )
            )
        availability_order = {
            "ON_SALE": 0, "RESERVATION": 1, "PRE_SALE": 2,
            "OUT_OF_STOCK": 3, "UNKNOWN": 4, "OFF_SHELF": 5,
        }
        return DeviceMatchCandidate(
            record=record,
            product_key=(record.brand_code.upper(), identifier),
            brand_code=record.brand_code,
            brand_name=record.brand_name,
            product_name=record.product_name,
            series_name=record.series_name,
            model_number=record.model_number,
            capacities=extract_capacity_tokens(
                record.capacity, record.memory, record.sku_name
            ),
            order_key=(
                availability_order.get(record.availability, 4),
                -record.observed_at.timestamp(),
                str(record.offer_id).zfill(20),
            ),
        )

    def _to_evidence(
        self,
        index: int,
        ranked: RankedDeviceCandidate[DevicePriceRecord],
    ) -> DevicePriceEvidence:
        record = ranked.record
        specification = self._specification(record)
        title = record.product_name
        if (
            record.brand_name
            and normalize_text(record.brand_name) not in normalize_text(title)
        ):
            title = f"{record.brand_name} {title}"
        return DevicePriceEvidence(
            evidence_id=f"price-{index}",
            title=title,
            brand=record.brand_name or record.brand_code,
            model=record.product_name,
            specification=specification,
            price=self._format_price(record.current_price),
            currency=record.currency,
            source=record.channel_name,
            observed_at=self._format_observed_at(record.observed_at),
            availability=record.availability,
            source_url=record.source_url or record.official_product_url,
            original_price=(
                self._format_price(record.original_price)
                if record.original_price is not None
                else None
            ),
            original_price_type=record.original_price_type,
            official_product_id=record.official_product_id,
            official_sku_id=record.official_sku_id,
            match_score=round(ranked.score, 3),
        )

    @staticmethod
    def _specification(record: DevicePriceRecord) -> str:
        values: list[str] = []
        for raw in (
            record.capacity,
            record.memory,
            record.connectivity,
            record.size,
            record.color,
        ):
            value = raw.split("脚注", 1)[0].strip()
            if value and value not in values:
                values.append(value)
        return " / ".join(values) or record.sku_name

    @staticmethod
    def _format_price(value: Decimal) -> str:
        return format(value, ".2f")

    @staticmethod
    def _format_observed_at(value: datetime) -> str:
        if value.tzinfo is None:
            return f"{value.isoformat()}Z"
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def readiness(self) -> str:
        return self._repository.readiness()

    async def close(self) -> None:
        await self._repository.close()
