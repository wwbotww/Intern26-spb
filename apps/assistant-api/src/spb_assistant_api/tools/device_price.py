from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from rapidfuzz import fuzz

from ..domain.device_price import (
    DevicePriceRecord,
    DevicePriceSearchQuery,
)
from ..domain.exceptions import (
    PriceRepositoryUnavailableError,
    ToolUnavailableError,
)
from ..domain.models import DevicePriceEvidence, ToolResult, ToolStatus
from ..domain.ports import DevicePriceRepository
from .device_query import (
    BRAND_ALIASES,
    MODEL_VARIANT_WORDS,
    PRODUCT_FAMILY_TOKENS,
    ParsedDeviceQuery,
    extract_capacity_tokens,
    normalize_text,
    parse_device_query,
)
from .query_scope import cross_category_result, is_cross_category_question


DEVICE_PRICE_TOOL_NAME = "device_price"


@dataclass(frozen=True, slots=True)
class RankedPrice:
    record: DevicePriceRecord
    score: float


@dataclass(frozen=True, slots=True)
class RankedProduct:
    records: tuple[DevicePriceRecord, ...]
    score: float


IDENTITY_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
IDENTITY_WORD_RE = re.compile(r"[a-z0-9]+")


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

        ranked = self._rank(parsed, records)
        if not ranked:
            return ToolResult(
                tool=self.name,
                status=ToolStatus.NO_MATCH,
                answer=(
                    "未在当前设备价格库中找到足够匹配的型号记录。"
                    "请核对品牌、完整型号和容量后重新查询。"
                ),
            )

        if parsed.capacities:
            spec_matches = [
                item
                for item in ranked
                if set(parsed.capacities).issubset(
                    set(self._record_capacities(item.record))
                )
            ]
            if not spec_matches:
                return ToolResult(
                    tool=self.name,
                    status=ToolStatus.NO_MATCH,
                    answer=(
                        "找到了可能的设备型号，但没有找到与所给容量或内存"
                        "规格一致的价格记录。请核对完整规格后重新查询。"
                    ),
                    missing_fields=("matching_specification",),
                )
            ranked = spec_matches

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

    def _rank(
        self,
        parsed: ParsedDeviceQuery,
        records: list[DevicePriceRecord],
    ) -> list[RankedPrice]:
        grouped: dict[
            tuple[str, str],
            list[DevicePriceRecord],
        ] = {}
        for record in records:
            grouped.setdefault(self._product_key(record), []).append(record)

        products: list[RankedProduct] = []
        for product_records in grouped.values():
            representative = product_records[0]
            if (
                parsed.brand_code is not None
                and representative.brand_code.upper() != parsed.brand_code
            ):
                continue
            if not self._matches_required_identity(parsed, representative):
                continue
            score = self._score_product(parsed, representative)
            if score < self._match_threshold:
                continue
            ordered_records = tuple(
                sorted(product_records, key=self._record_order_key)
            )
            products.append(
                RankedProduct(records=ordered_records, score=score)
            )

        products.sort(
            key=lambda item: (
                -item.score,
                self._record_order_key(item.records[0]),
                self._product_key(item.records[0]),
            )
        )
        if products:
            best_score = products[0].score
            products = [
                product
                for product in products
                if abs(product.score - best_score) < 0.001
            ]
        return [
            RankedPrice(record=record, score=product.score)
            for product in products
            for record in product.records
        ]

    @staticmethod
    def _product_key(record: DevicePriceRecord) -> tuple[str, str]:
        identifier = record.official_product_id.strip()
        if not identifier:
            identifier = "|".join(
                normalize_text(value)
                for value in (
                    record.product_name,
                    record.series_name,
                    record.model_number,
                )
            )
        return record.brand_code.upper(), identifier

    @classmethod
    def _matches_required_identity(
        cls,
        parsed: ParsedDeviceQuery,
        record: DevicePriceRecord,
    ) -> bool:
        query = parsed.model_text
        product_identity = " ".join(
            value
            for value in (
                record.product_name,
                record.series_name,
                record.model_number,
            )
            if value
        )
        query_compact = cls._compact_identity(query)
        product_compact = cls._compact_identity(product_identity)

        requested_families = {
            family
            for family in PRODUCT_FAMILY_TOKENS
            if family in query_compact
        }
        if any(
            family not in product_compact
            for family in requested_families
        ):
            return False

        query_numbers = set(IDENTITY_NUMBER_RE.findall(query))
        product_numbers = set(
            IDENTITY_NUMBER_RE.findall(normalize_text(product_identity))
        )
        if not query_numbers.issubset(product_numbers):
            return False

        query_mixed_tokens = cls._mixed_model_tokens(query)
        if any(
            token not in product_compact for token in query_mixed_tokens
        ):
            return False

        requested_variants = cls._variant_words(query)
        product_variants = cls._variant_words(product_identity)
        return requested_variants.issubset(product_variants)

    @staticmethod
    def _score_product(
        parsed: ParsedDeviceQuery,
        record: DevicePriceRecord,
    ) -> float:
        fields = (
            record.product_name,
            record.series_name,
            record.model_number,
        )
        normalized_fields: list[str] = []
        for value in fields:
            if not value:
                continue
            normalized = normalize_text(value)
            normalized_fields.append(normalized)
            without_brand = DevicePriceTool._without_brand_prefix(
                normalized,
                record,
            )
            if without_brand != normalized:
                normalized_fields.append(without_brand)
        if not normalized_fields:
            return 0.0

        score = max(
            float(fuzz.WRatio(parsed.model_text, value))
            for value in normalized_fields
        )
        compact_query = DevicePriceTool._compact_identity(parsed.model_text)
        compact_fields = [
            DevicePriceTool._compact_identity(value)
            for value in normalized_fields
        ]
        if compact_query in compact_fields:
            score = 100.0
        elif compact_query and any(
            compact_query in value for value in compact_fields
        ):
            score = max(score, 95.0)
        if parsed.brand_code == record.brand_code.upper():
            score += 3
        return min(100.0, score)

    @staticmethod
    def _compact_identity(value: str) -> str:
        normalized = normalize_text(value).replace("+", "plus")
        return "".join(
            character
            for character in normalized
            if character.isalnum()
            or "\u4e00" <= character <= "\u9fff"
        )

    @staticmethod
    def _without_brand_prefix(
        normalized: str,
        record: DevicePriceRecord,
    ) -> str:
        prefixes = {
            *BRAND_ALIASES.get(record.brand_code.upper(), ()),
            normalize_text(record.brand_name),
            record.brand_code.lower(),
        }
        for prefix in sorted(prefixes, key=len, reverse=True):
            if not prefix or not normalized.startswith(prefix):
                continue
            remainder = normalized[len(prefix) :].lstrip(" .+-")
            if remainder:
                return remainder
        return normalized

    @staticmethod
    def _mixed_model_tokens(value: str) -> frozenset[str]:
        tokens = IDENTITY_WORD_RE.findall(normalize_text(value))
        return frozenset(
            token
            for token in tokens
            if any(character.isalpha() for character in token)
            and any(character.isdigit() for character in token)
        )

    @staticmethod
    def _variant_words(value: str) -> frozenset[str]:
        normalized = normalize_text(value)
        tokens = IDENTITY_WORD_RE.findall(normalized)
        variants = {
            variant
            for variant in MODEL_VARIANT_WORDS
            if variant in tokens
            or any(token.endswith(variant) for token in tokens)
        }
        if "+" in normalized:
            variants.add("plus")
        return frozenset(variants)

    @classmethod
    def _record_order_key(
        cls,
        record: DevicePriceRecord,
    ) -> tuple[int, float, int]:
        return (
            cls._availability_rank(record.availability),
            -record.observed_at.timestamp(),
            record.offer_id,
        )

    @staticmethod
    def _record_capacities(
        record: DevicePriceRecord,
    ) -> tuple[str, ...]:
        return extract_capacity_tokens(
            record.capacity,
            record.memory,
            record.sku_name,
        )

    @staticmethod
    def _availability_rank(value: str) -> int:
        order = {
            "ON_SALE": 0,
            "RESERVATION": 1,
            "PRE_SALE": 2,
            "OUT_OF_STOCK": 3,
            "UNKNOWN": 4,
            "OFF_SHELF": 5,
        }
        return order.get(value, 4)

    def _to_evidence(
        self,
        index: int,
        ranked: RankedPrice,
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
