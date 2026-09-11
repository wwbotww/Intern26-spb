"""Product-first device matching, independent of persistence and wire DTOs.

Entry points project only proven product identity into these candidates. SKU
attributes contribute capacities after product ranking, never model identity.
The generic payload is returned unchanged, so V2 facts need no legacy record.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from rapidfuzz import fuzz

from ..domain.device_query import (
    BRAND_ALIASES,
    MODEL_VARIANT_WORDS,
    PRODUCT_FAMILY_TOKENS,
    ParsedDeviceQuery,
    normalize_text,
)


T = TypeVar("T")
IDENTITY_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
IDENTITY_WORD_RE = re.compile(r"[a-z0-9]+")
VARIANT_CHAIN_RE = re.compile(
    r"(?:" + "|".join(MODEL_VARIANT_WORDS) + r")+$"
)
VARIANT_TOKEN_RE = re.compile("|".join(MODEL_VARIANT_WORDS))


@dataclass(frozen=True, slots=True)
class DeviceMatchCandidate(Generic[T]):
    record: T
    product_key: tuple[str, str]
    brand_code: str
    brand_name: str
    product_name: str
    series_name: str
    model_number: str
    capacities: tuple[str, ...]
    order_key: tuple[int, float, str]


@dataclass(frozen=True, slots=True)
class RankedDeviceCandidate(Generic[T]):
    record: T
    score: float


@dataclass(frozen=True, slots=True)
class DeviceMatchResult(Generic[T]):
    ranked: tuple[RankedDeviceCandidate[T], ...]
    specification_mismatch: bool = False


class DevicePriceMatchingStrategy:
    """Hard identity gates precede fuzzy ranking and exact capacity filtering."""

    def match(
        self,
        parsed: ParsedDeviceQuery,
        candidates: Sequence[DeviceMatchCandidate[T]],
        *,
        match_threshold: float,
    ) -> DeviceMatchResult[T]:
        if not parsed.sufficient:
            return DeviceMatchResult(ranked=())
        grouped: dict[tuple[str, str], list[DeviceMatchCandidate[T]]] = {}
        for candidate in candidates:
            if (
                parsed.brand_code is not None
                and candidate.brand_code.upper() != parsed.brand_code
            ):
                continue
            if not self._matches_required_identity(parsed, candidate):
                continue
            grouped.setdefault(candidate.product_key, []).append(candidate)

        products: list[tuple[float, tuple[DeviceMatchCandidate[T], ...]]] = []
        for group in grouped.values():
            ordered = tuple(sorted(group, key=lambda candidate: candidate.order_key))
            score = self._score_product(parsed, ordered[0])
            if score >= match_threshold:
                products.append((score, ordered))
        products.sort(
            key=lambda product: (
                -product[0], product[1][0].order_key, product[1][0].product_key
            )
        )
        if not products:
            return DeviceMatchResult(ranked=())

        best_score = products[0][0]
        ranked = [
            (score, candidate)
            for score, group in products
            if abs(score - best_score) < 0.001
            for candidate in group
        ]
        if parsed.capacities:
            ranked = [
                (score, candidate)
                for score, candidate in ranked
                if set(parsed.capacities).issubset(candidate.capacities)
            ]
            if not ranked:
                return DeviceMatchResult(ranked=(), specification_mismatch=True)
        return DeviceMatchResult(
            ranked=tuple(
                RankedDeviceCandidate(record=candidate.record, score=score)
                for score, candidate in ranked
            )
        )

    @classmethod
    def _matches_required_identity(
        cls, parsed: ParsedDeviceQuery, candidate: DeviceMatchCandidate[T]
    ) -> bool:
        query = parsed.model_text
        product_identity = " ".join(
            value
            for value in (
                candidate.product_name, candidate.series_name, candidate.model_number
            )
            if value
        )
        query_compact = cls._compact_identity(query)
        product_compact = cls._compact_identity(product_identity)
        requested_families = {
            family for family in PRODUCT_FAMILY_TOKENS if family in query_compact
        }
        if any(family not in product_compact for family in requested_families):
            return False
        query_numbers = set(IDENTITY_NUMBER_RE.findall(query))
        product_numbers = set(
            IDENTITY_NUMBER_RE.findall(normalize_text(product_identity))
        )
        if not query_numbers.issubset(product_numbers):
            return False
        if any(
            token not in product_compact for token in cls._mixed_model_tokens(query)
        ):
            return False

        # Variants are model identity, not optional adjectives. A shared series
        # name must not admit Pro when the exact base model was requested, and
        # Pro Max must not serve as a substitute when Pro has no data.
        return cls._variant_words(query) == cls._variant_words(product_identity)

    @classmethod
    def _score_product(
        cls, parsed: ParsedDeviceQuery, candidate: DeviceMatchCandidate[T]
    ) -> float:
        normalized_fields: list[str] = []
        for value in (
            candidate.product_name, candidate.series_name, candidate.model_number
        ):
            if not value:
                continue
            normalized = normalize_text(value)
            normalized_fields.append(normalized)
            without_brand = cls._without_brand_prefix(normalized, candidate)
            if without_brand != normalized:
                normalized_fields.append(without_brand)
        if not normalized_fields:
            return 0.0
        score = max(
            float(fuzz.WRatio(parsed.model_text, value)) for value in normalized_fields
        )
        compact_query = cls._compact_identity(parsed.model_text)
        compact_fields = [cls._compact_identity(value) for value in normalized_fields]
        if compact_query in compact_fields:
            score = 100.0
        elif compact_query and any(compact_query in value for value in compact_fields):
            score = max(score, 95.0)
        if parsed.brand_code == candidate.brand_code.upper():
            score += 3
        return min(100.0, score)

    @staticmethod
    def _compact_identity(value: str) -> str:
        normalized = normalize_text(value).replace("+", "plus")
        return "".join(
            character for character in normalized
            if character.isalnum() or "\u4e00" <= character <= "\u9fff"
        )

    @staticmethod
    def _without_brand_prefix(
        normalized: str, candidate: DeviceMatchCandidate[T]
    ) -> str:
        prefixes = {
            *BRAND_ALIASES.get(candidate.brand_code.upper(), ()),
            normalize_text(candidate.brand_name),
            candidate.brand_code.lower(),
        }
        for prefix in sorted(prefixes, key=len, reverse=True):
            if not prefix or not normalized.startswith(prefix):
                continue
            remainder = normalized[len(prefix):].lstrip(" .+-")
            if remainder:
                return remainder
        return normalized

    @staticmethod
    def _mixed_model_tokens(value: str) -> frozenset[str]:
        return frozenset(
            token for token in IDENTITY_WORD_RE.findall(normalize_text(value))
            if any(character.isalpha() for character in token)
            and any(character.isdigit() for character in token)
        )

    @staticmethod
    def _variant_words(value: str) -> frozenset[str]:
        normalized = normalize_text(value)
        variants: set[str] = set()
        for token in IDENTITY_WORD_RE.findall(normalized):
            # Recognize compact 16ProMax as the same two explicit variants as
            # 16 Pro Max, preserving the old explicit/suffixed variant grammar.
            match = VARIANT_CHAIN_RE.search(token)
            if match is not None:
                variants.update(VARIANT_TOKEN_RE.findall(match.group()))
        if "+" in normalized:
            variants.add("plus")
        return frozenset(variants)
