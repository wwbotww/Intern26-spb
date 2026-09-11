"""Shared bounded price reads. Final device ranking and Agent routing live later.

This service borrows a read Repository; the composition root owns its lifetime.
It neither initializes another pool nor turns candidates into a final quotation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import timedelta

from ..domain.exceptions import ProductPriceContractError
from ..domain.ports import ProductPriceReadRepository
from ..domain.product_price_query import (
    PRODUCT_PRICE_READ_QUERY,
    DevicePriceReadQuery,
    PriceFreshness,
    PriceReadBatch,
    ProductPriceCandidateFact,
    ProductPriceQueryResult,
    ProductPriceReadQuery,
)


class ProductPriceQueryService:
    def __init__(
        self,
        repository: ProductPriceReadRepository,
        *,
        freshness_max_age_seconds: Mapping[str, int] | None = None,
    ) -> None:
        self._repository = repository
        self._freshness = dict(freshness_max_age_seconds or {})
        for channel, seconds in self._freshness.items():
            if not isinstance(channel, str) or not channel.strip():
                raise ValueError("freshness thresholds require explicit source channel codes")
            if type(seconds) is not int or not 0 < seconds <= 366 * 24 * 60 * 60:
                raise ValueError("freshness age must be an explicit positive number of seconds within a year")

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
            if identity.kind != query.kind:
                raise ProductPriceContractError("价格读取返回了不同类目的事实")
            if isinstance(query, DevicePriceReadQuery):
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
        if isinstance(query, DevicePriceReadQuery):
            if len(products) > query.product_limit or any(
                count > query.per_product_limit for count in products.values()
            ):
                raise ProductPriceContractError("设备读取超过产品或规格候选预算")
        elif len(batch.records) > query.listing_limit:
            raise ProductPriceContractError("生鲜读取超过候选预算")
