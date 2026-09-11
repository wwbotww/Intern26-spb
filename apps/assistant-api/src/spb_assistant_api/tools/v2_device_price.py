"""Thin old-protocol price projection over the V2 core; owns no repository.

The existing V1/Agent device card requires a numeric price. Amountless current
facts stay matched internally, but this old protocol returns a state explanation
with empty price evidence and an explicit reason, never a fabricated amount.
"""

from __future__ import annotations

from datetime import timezone

from ..domain.device_price_quote import DevicePriceQuoteCandidate
from ..domain.device_query import normalize_text
from ..domain.exceptions import (
    PriceRepositoryUnavailableError, ProductPriceContractError,
    ProductPriceTimeoutError, ToolContractError, ToolUnavailableError,
)
from ..domain.models import DevicePriceEvidence, ToolResult, ToolStatus
from ..services.product_price_query import ProductPriceQueryService
from .query_scope import cross_category_result, is_cross_category_question


class V2DevicePriceTool:
    def __init__(self, *, service: ProductPriceQueryService) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return "device_price"

    async def initialize(self) -> None:
        # The application composition root owns this resource, not the wrapper.
        return None

    async def close(self) -> None:
        return None

    def readiness(self) -> str:
        return self._service.readiness()

    async def execute(self, question: str) -> ToolResult:
        if is_cross_category_question(question):
            return cross_category_result(self.name)
        try:
            result = await self._service.quote_device(question)
        except ProductPriceContractError:
            raise ToolContractError("价格数据或读取条件未通过校验") from None
        except ProductPriceTimeoutError:
            # Preserve the existing Agent timeout taxonomy without relying on
            # a driver exception chain that the Repository deliberately redacts.
            raise TimeoutError("价格查询超时") from None
        except PriceRepositoryUnavailableError:
            raise ToolUnavailableError(self.name) from None

        if result.status == "need_more_info":
            incomplete = result.reason_code == "price_candidates_incomplete"
            return ToolResult(
                tool=self.name, status=ToolStatus.NEED_MORE_INFO,
                answer=(
                    "候选记录达到本次查询上限，尚不能确认所需规格是否存在。请提供更完整的品牌、型号和规格后重新查询。"
                    if incomplete else "请在本次问题中提供完整的设备品牌和型号，如需指定版本，请同时提供容量或内存规格。"
                ),
                missing_fields=("brand_or_model",), reason_code=result.reason_code,
            )
        if result.status == "no_match":
            specification = result.reason_code == "no_matching_specification"
            return ToolResult(
                tool=self.name, status=ToolStatus.NO_MATCH,
                answer=(
                    "找到了可能的设备型号，但没有找到与所给容量或内存规格一致的价格记录。请核对完整规格后重新查询。"
                    if specification else "未在当前设备价格库中找到足够匹配的型号记录。请核对品牌、完整型号和容量后重新查询。"
                ),
                missing_fields=("matching_specification",) if specification else (),
                reason_code=result.reason_code,
            )

        priced = tuple(item for item in result.candidates if item.record.observation.kind == "priced")
        amountless = tuple(item for item in result.candidates if item.record.observation.kind == "availability_only")
        state_lines = tuple(self._state_description(item) for item in amountless)
        warnings = []
        if len(result.candidates) > 1:
            warnings.append("同一型号可能包含多个 SKU，以下价格均为候选参考记录。")
        if result.truncated:
            warnings.append(
                "匹配结果超过查询上限，仅返回本次有限候选；不代表全部规格或最低价范围。"
                if result.recall_truncated else f"匹配结果超过展示上限，仅返回前 {len(result.candidates)} 条。"
            )
        if any(item.record.observation.availability not in {"ON_SALE", "RESERVATION", "PRE_SALE"} for item in result.candidates):
            warnings.append("部分候选当前不是在售状态，请结合状态和观察时间判断。")
        if any(item.freshness.status == "stale" for item in result.candidates):
            warnings.append("部分观察已超过来源的更新周期阈值，不能视为实时价格。")
        if any(item.freshness.status == "unknown" for item in result.candidates):
            warnings.append("价格有效性请结合来源与观察时间判断，不保证为实时成交报价。")

        if not priced:
            if result.truncated:
                # Either recall or display can hide a priced specification.
                # The visible state-only subset does not prove no quote exists.
                return ToolResult(
                    tool=self.name, status=ToolStatus.NEED_MORE_INFO,
                    answer=(
                        "本次有限候选中只展示了无金额状态，尚不能确认其他规格是否有报价。"
                        "请提供更完整的型号和规格后重新查询。\n" + "\n".join(state_lines)
                    ),
                    missing_fields=("brand_or_model",),
                    reason_code="price_candidates_incomplete", warnings=tuple(warnings),
                )
            # Old NO_MATCH means no numeric quotation on this protocol only;
            # the explicit reason and answer distinguish found state evidence.
            return ToolResult(
                tool=self.name, status=ToolStatus.NO_MATCH,
                answer="已找到设备状态记录，但当前没有可用报价。\n" + "\n".join(state_lines),
                reason_code="current_price_unavailable", warnings=tuple(warnings),
            )
        return ToolResult(
            tool=self.name,
            status=ToolStatus.PARTIAL if amountless or result.recall_truncated else ToolStatus.SUCCESS,
            answer=(
                f"查询到 {len(priced)} 条可能匹配的设备参考价格记录。"
                "价格来自已采集的官方商城数据，不代表最终定损或赔付金额。"
                + ("\n另有无报价状态：\n" + "\n".join(state_lines) if state_lines else "")
            ),
            evidence=tuple(self._evidence(index, item) for index, item in enumerate(priced, start=1)),
            warnings=tuple(warnings),
            reason_code="current_price_partially_unavailable" if amountless else (
                "price_candidates_incomplete" if result.recall_truncated else ""
            ),
        )

    @staticmethod
    def _specification(item: DevicePriceQuoteCandidate) -> str:
        spec = item.record.listing.identity.specification
        values = []
        for raw in (spec.capacity, spec.memory, spec.connectivity, spec.size, spec.color, spec.edition, spec.manufacturer_part_number):
            value = (raw or "").split("脚注", 1)[0].strip()
            if value and value not in values:
                values.append(value)
        values.extend(f"{key}: {value}" for key, value in sorted(spec.attributes.items()))
        return " / ".join(values)

    @staticmethod
    def _observed_at(item: DevicePriceQuoteCandidate) -> str:
        return item.record.observation.observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @classmethod
    def _state_description(cls, item: DevicePriceQuoteCandidate) -> str:
        record = item.record
        identity = record.listing.identity
        state = {"OFF_SHELF": "已下架", "OUT_OF_STOCK": "缺货", "COMING_SOON": "待上市"}[record.observation.availability]
        return (
            f"{identity.product_name}（{cls._specification(item)}）：{state}，无当前金额；"
            f"来源：{record.listing.source.channel_name}，观察时间：{cls._observed_at(item)}，"
            f"来源链接：{record.listing.source.source_url}"
        )

    @classmethod
    def _evidence(cls, index: int, item: DevicePriceQuoteCandidate) -> DevicePriceEvidence:
        record = item.record
        identity, observation = record.listing.identity, record.observation
        title = identity.product_name
        if identity.brand_name and normalize_text(identity.brand_name) not in normalize_text(title):
            title = f"{identity.brand_name} {title}"
        return DevicePriceEvidence(
            evidence_id=f"price-{index}", title=title,
            brand=identity.brand_name or identity.brand_code, model=identity.product_name,
            specification=cls._specification(item), price=format(observation.current_price, ".2f"),
            currency=observation.currency, source=record.listing.source.channel_name,
            observed_at=cls._observed_at(item), availability=observation.availability,
            source_url=str(record.listing.source.source_url),
            original_price=format(observation.original_price, ".2f") if observation.original_price is not None else None,
            original_price_type=observation.original_price_type,
            official_product_id=identity.official_product_id,
            official_sku_id=identity.official_sku_id or "", match_score=round(item.match_score, 3),
        )
