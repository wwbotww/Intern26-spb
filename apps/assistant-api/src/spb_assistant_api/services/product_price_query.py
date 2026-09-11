"""Shared bounded price reads and product-first device quotations.

This service borrows a read Repository; the composition root owns its lifetime.
It returns typed current facts, including amountless states. Agent routing and
old/new HTTP projections remain outside the business core.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import timedelta
import math
from zoneinfo import ZoneInfo

from ..domain.device_price_quote import (
    DevicePriceQuoteCandidate,
    DevicePriceQuoteResult,
    DevicePriceSpecificationFilter,
)
from ..domain.device_query import extract_capacity_tokens, normalize_capacity, normalize_text, parse_device_query
from ..domain.exceptions import ProductPriceContractError
from ..domain.ports import ProductPriceReadRepository
from ..domain.product_price_execution import ProductPriceQuote, PriceSelectionReference
from ..domain.product_price_slots import ProductPriceCommand
from ..domain.product_price_query import (
    PRODUCT_PRICE_READ_QUERY,
    DevicePriceReadQuery,
    FreshPriceReadQuery,
    PriceFreshness,
    PriceReadBatch,
    ProductPriceCandidateFact,
    ProductPriceQueryResult,
    ProductPriceReadQuery,
    SelectedPriceReadQuery,
)
from .device_price_matching import DeviceMatchCandidate, DevicePriceMatchingStrategy
from .fresh_price_scope import fresh_regions, UnsupportedFreshScope


class ProductPriceQueryService:
    def __init__(
        self,
        repository: ProductPriceReadRepository,
        *,
        freshness_max_age_seconds: Mapping[str, int] | None = None,
        product_limit: int = 20,
        per_product_limit: int = 50,
        result_limit: int = 50,
        match_threshold: float = 65.0,
    ) -> None:
        self._repository = repository
        # Use the same budget contract as a direct Repository caller.
        bounds = DevicePriceReadQuery(
            terms=("budget-validation",), product_limit=product_limit,
            per_product_limit=per_product_limit,
        )
        if type(result_limit) is not int or not 1 <= result_limit <= min(100, product_limit * per_product_limit):
            raise ValueError("device result limit must fit the bounded recall and public display budget")
        if isinstance(match_threshold, bool) or not isinstance(match_threshold, (int, float)) or not math.isfinite(match_threshold) or not 0 <= match_threshold <= 100:
            raise ValueError("device match threshold must be finite and between zero and one hundred")
        self._product_limit = bounds.product_limit
        self._per_product_limit = bounds.per_product_limit
        self._result_limit = result_limit
        self._match_threshold = float(match_threshold)
        self._device_strategy = DevicePriceMatchingStrategy()
        self._freshness = dict(freshness_max_age_seconds or {})
        for channel, seconds in self._freshness.items():
            if not isinstance(channel, str) or not channel.strip():
                raise ValueError("freshness thresholds require explicit source channel codes")
            if type(seconds) is not int or not 0 < seconds <= 366 * 24 * 60 * 60:
                raise ValueError("freshness age must be an explicit positive number of seconds within a year")

    def readiness(self) -> str:
        return self._repository.readiness()

    async def quote_product(self, command: ProductPriceCommand) -> ProductPriceQuote:
        command = ProductPriceCommand.model_validate(command.model_dump())
        conditions = command.conditions
        if command.selection is not None:
            reference = command.selection
            if reference.kind != conditions.kind:
                raise ProductPriceContractError("所选商品类目与请求不同")
            batch = await self.search(SelectedPriceReadQuery(
                category_kind=reference.kind, source_listing_id=reference.source_listing_id,
                region=reference.region,
            ))
            if not batch.candidates or PriceSelectionReference.from_record(batch.candidates[0].record) != reference:
                return ProductPriceQuote(status="no_match", reason_code="price_selection_identity_changed")
            if not self.matches_conditions(command, batch.candidates[0]):
                raise ProductPriceContractError("所选价格身份不符合当前查询条件")
            facts, truncated = batch.candidates, False
        elif conditions.kind == "device":
            question = " ".join(value for value in (conditions.brand, conditions.product_text) if value)
            result = await self.quote_device(question, specification=conditions.specification)
            if result.status == "no_match":
                return ProductPriceQuote(status="no_match", reason_code=result.reason_code)
            if not result.candidates:
                return ProductPriceQuote(status="need_more_info", reason_code=result.reason_code,
                                         missing_slots=("conditions.product_text",))
            facts = tuple(ProductPriceCandidateFact(record=item.record, freshness=item.freshness)
                          for item in result.candidates)
            truncated = result.truncated or result.recall_truncated
        else:
            if conditions.requested_unit is not None and conditions.requested_unit.unit == "EACH":
                raise UnsupportedFreshScope("当前生鲜来源按重量计价，无法按个换算。")
            regions = fresh_regions(conditions)
            candidates = []
            truncated = False
            for region in regions:
                batch = await self.search(FreshPriceReadQuery(
                    terms=(conditions.commodity,), region=region,
                    price_nature=conditions.price_nature, listing_limit=50,
                ))
                truncated |= batch.truncated
                for fact in batch.candidates:
                    identity = fact.record.listing.identity
                    # Wide text recall is not proof of the requested commodity.
                    if normalize_text(identity.commodity_name) != normalize_text(conditions.commodity):
                        continue
                    if conditions.variety and normalize_text(conditions.variety) != normalize_text(identity.source_specification):
                        continue
                    if conditions.market_text and normalize_text(conditions.market_text) != normalize_text(identity.source_market_name or ""):
                        continue
                    candidates.append(fact)
            facts = tuple(candidates)
            if not facts:
                return ProductPriceQuote(
                    status="need_more_info" if truncated else "no_match",
                    reason_code="price_candidates_incomplete" if truncated else "no_matching_fresh_scope",
                    missing_slots=("conditions.region_text", "conditions.market_text") if truncated else (),
                )

        warnings: list[str] = []
        if truncated:
            warnings.append("仅返回有界范围内的候选，不代表全部商品、市场或最低价。")
        if any(item.freshness.status == "stale" for item in facts):
            warnings.append("部分价格观察已超过来源新鲜度阈值。")
        if any(item.freshness.status == "unknown" for item in facts):
            warnings.append("来源尚未设置新鲜度阈值，请核对观察时间。")
        if command.time.kind == "today":
            china = ZoneInfo("Asia/Shanghai")
            if any(item.record.observation.observed_at.astimezone(china).date()
                   != item.record.read_at.astimezone(china).date() for item in facts):
                warnings.append("返回的是最新可用观察，其中存在非今日数据，不能作为今日报价。")
        separate = conditions.kind == "fresh" and conditions.source_scope == "separate_sources"
        needs_choice = command.selection is None and (len(facts) > 1 or truncated) and not separate
        return ProductPriceQuote(
            status="candidates" if needs_choice else "quote", facts=facts,
            truncated=truncated, reason_code="price_candidates_found" if needs_choice else "",
            warnings=tuple(warnings),
        )

    async def quote_device(
        self,
        question: str,
        *,
        specification: DevicePriceSpecificationFilter | None = None,
    ) -> DevicePriceQuoteResult:
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise ProductPriceContractError("设备查询文本无效")
        if specification is not None:
            specification = DevicePriceSpecificationFilter.model_validate(
                specification.model_dump() if isinstance(specification, DevicePriceSpecificationFilter) else specification,
            )
        parsed = parse_device_query(question)
        if not parsed.sufficient:
            return DevicePriceQuoteResult(status="need_more_info", reason_code="missing_device_identity")
        # The parser also includes shorter tokens/family hints. Never split or
        # truncate a long model name into a different identity to fit SQL limits.
        terms = tuple(term for term in parsed.terms if len(term) <= 100)
        if not terms:
            return DevicePriceQuoteResult(status="need_more_info", reason_code="missing_device_identity")
        result = await self.search(DevicePriceReadQuery(
            terms=terms, brand_code=parsed.brand_code,
            product_limit=self._product_limit, per_product_limit=self._per_product_limit,
        ))
        candidates = [self._device_candidate(fact) for fact in result.candidates]
        matched = self._device_strategy.match(parsed, candidates, match_threshold=self._match_threshold)
        ranked = tuple(item for item in matched.ranked if specification is None or self._matches_specification(item.record, specification))
        if not ranked:
            if result.truncated:
                return DevicePriceQuoteResult(
                    status="need_more_info", reason_code="price_candidates_incomplete",
                    truncated=True, recall_truncated=True,
                )
            return DevicePriceQuoteResult(
                status="no_match",
                reason_code="no_matching_specification" if matched.specification_mismatch or matched.ranked else "no_matching_device",
            )
        return DevicePriceQuoteResult(
            status="matched",
            candidates=tuple(DevicePriceQuoteCandidate(
                record=item.record.record, freshness=item.record.freshness, match_score=item.score,
            ) for item in ranked[:self._result_limit]),
            truncated=result.truncated or len(ranked) > self._result_limit,
            recall_truncated=result.truncated,
        )

    @staticmethod
    def _device_candidate(fact: ProductPriceCandidateFact):
        identity = fact.record.listing.identity
        spec = identity.specification
        observation = fact.record.observation
        availability_order = {
            "ON_SALE": 0, "RESERVATION": 1, "PRE_SALE": 2,
            "OUT_OF_STOCK": 3, "UNKNOWN": 4, "COMING_SOON": 4, "OFF_SHELF": 5,
        }
        return DeviceMatchCandidate(
            record=fact, product_key=(identity.brand_code, str(identity.catalog_item_id)),
            brand_code=identity.brand_code, brand_name=identity.brand_name,
            product_name=identity.product_name, series_name=identity.series_name or "",
            model_number=identity.model_number or "",
            capacities=extract_capacity_tokens(spec.capacity or "", spec.memory or ""),
            order_key=(availability_order[observation.availability], -observation.observed_at.timestamp(),
                       f"{fact.record.listing.source_listing_id:020d}"),
        )

    @classmethod
    def matches_conditions(cls, command: ProductPriceCommand, fact: ProductPriceCandidateFact) -> bool:
        """Defense for selected reads and restored results, without another DB call."""
        conditions, record = command.conditions, fact.record
        identity = record.listing.identity
        if conditions.kind != identity.kind:
            return False
        if conditions.kind == "device":
            parsed = parse_device_query(" ".join(value for value in (conditions.brand, conditions.product_text) if value))
            return bool(DevicePriceMatchingStrategy().match(parsed, [cls._device_candidate(fact)], match_threshold=65).ranked) and cls._matches_specification(fact, conditions.specification)
        regions = fresh_regions(conditions)
        return (
            (None in regions or record.pointer.region in regions)
            and (conditions.price_nature is None or conditions.price_nature == record.observation.price_nature)
            and normalize_text(conditions.commodity) == normalize_text(identity.commodity_name)
            and (not conditions.variety or normalize_text(conditions.variety) == normalize_text(identity.source_specification))
            and (not conditions.market_text or normalize_text(conditions.market_text) == normalize_text(identity.source_market_name or ""))
            and (conditions.requested_unit is None or conditions.requested_unit.unit in {"KG", "G", "JIN"})
        )

    @staticmethod
    def _matches_specification(fact: ProductPriceCandidateFact, requested: DevicePriceSpecificationFilter) -> bool:
        actual = fact.record.listing.identity.specification
        for field in (
            "capacity", "memory", "color", "connectivity", "size", "edition", "manufacturer_part_number",
        ):
            expected = getattr(requested, field)
            if expected is None:
                continue
            value = getattr(actual, field)
            normalize = normalize_capacity if field in {"capacity", "memory"} else normalize_text
            if value is None or normalize(value) != normalize(expected):
                return False
        return all(
            key in actual.attributes and normalize_text(actual.attributes[key]) == normalize_text(expected)
            for key, expected in requested.attributes.items()
        )

    async def search(self, query: ProductPriceReadQuery) -> ProductPriceQueryResult:
        validated = PRODUCT_PRICE_READ_QUERY.validate_python(query)
        batch = await self._repository.search(validated)
        self._validate_query_scope(validated, batch)
        candidates = []
        for record in batch.records:
            max_age = self._freshness.get(record.listing.source.channel_code)
            observed = record.observation.observed_at
            age = record.read_at - observed
            status = "unknown" if max_age is None else (
                "fresh" if age <= timedelta(seconds=max_age) else "stale"
            )
            candidates.append(ProductPriceCandidateFact(
                record=record,
                freshness=PriceFreshness(
                    status=status,
                    observed_at=observed,
                    evaluated_at=record.read_at,
                    max_age_seconds=max_age,
                ),
            ))
        return ProductPriceQueryResult(
            status="candidates" if candidates else "no_match",
            candidates=tuple(candidates),
            truncated=batch.truncated,
        )

    @staticmethod
    def _validate_query_scope(query: ProductPriceReadQuery, batch: PriceReadBatch) -> None:
        products: Counter[int] = Counter()
        for record in batch.records:
            identity = record.listing.identity
            kind = query.category_kind if isinstance(query, SelectedPriceReadQuery) else query.kind
            if identity.kind != kind:
                raise ProductPriceContractError("价格读取返回了不同类目的事实")
            if isinstance(query, SelectedPriceReadQuery):
                if record.pointer.source_listing_id != query.source_listing_id or record.pointer.region != query.region:
                    raise ProductPriceContractError("价格读取替换了所选身份")
            elif isinstance(query, DevicePriceReadQuery):
                if (
                    query.brand_code is not None and identity.brand_code != query.brand_code
                    or query.category_code is not None and identity.category_code != query.category_code
                ):
                    raise ProductPriceContractError("设备读取返回了其他品牌或类目")
                products[identity.catalog_item_id] += 1
            elif (
                query.region is not None and record.observation.region != query.region
                or query.price_nature is not None and record.observation.price_nature != query.price_nature
                or query.commodity_code is not None and identity.commodity_code != query.commodity_code
                or query.market_id is not None and identity.source_market_id != query.market_id
            ):
                raise ProductPriceContractError("生鲜读取返回了不同地域、市场或价格口径")
        if isinstance(query, SelectedPriceReadQuery):
            if len(batch.records) > 1 or batch.truncated:
                raise ProductPriceContractError("精确价格读取必须返回至多一条完整当前事实")
        elif isinstance(query, DevicePriceReadQuery):
            if len(products) > query.product_limit or any(
                count > query.per_product_limit for count in products.values()
            ):
                raise ProductPriceContractError("设备读取超过产品或规格候选预算")
        elif len(batch.records) > query.listing_limit:
            raise ProductPriceContractError("生鲜读取超过候选预算")
