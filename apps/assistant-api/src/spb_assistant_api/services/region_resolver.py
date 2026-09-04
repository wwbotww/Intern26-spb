from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from ..domain.slots import (
    RegionCandidate,
    RegionRef,
    RegionResolution,
)


class RegionCatalogEntry(BaseModel):
    """One injectable administrative-region catalog row.

    The bundled rows are deliberately a small demo fixture. A reviewed data
    source can replace them without changing the understanding contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str = Field(min_length=1, max_length=128)
    aliases: tuple[str, ...] = Field(min_length=1)
    province_code: str | None = None
    city_code: str | None = None
    county_code: str | None = None

    def as_candidate(self) -> RegionCandidate:
        return RegionCandidate(
            canonical_name=self.canonical_name,
            province_code=self.province_code,
            city_code=self.city_code,
            county_code=self.county_code,
        )


@dataclass(frozen=True, slots=True)
class RegionMention:
    start: int
    end: int
    raw_text: str
    ref: RegionRef


class RegionResolver:
    def __init__(
        self,
        entries: tuple[RegionCatalogEntry, ...],
        *,
        catalog_version: str,
    ) -> None:
        if not entries:
            raise ValueError("行政区划目录不能为空")
        if not catalog_version.strip():
            raise ValueError("catalog_version 不能为空")
        self.catalog_version = catalog_version.strip()
        index: dict[str, list[RegionCatalogEntry]] = {}
        aliases: set[str] = set()
        for entry in entries:
            for raw_alias in (*entry.aliases, entry.canonical_name):
                alias = self._normalize(raw_alias)
                if not alias:
                    raise ValueError("行政区划别名不能为空")
                aliases.add(alias)
                bucket = index.setdefault(alias, [])
                if entry not in bucket:
                    bucket.append(entry)
        self._index = index
        self._aliases = tuple(sorted(aliases, key=len, reverse=True))

    def resolve(self, raw_text: str) -> RegionRef:
        normalized = self._normalize(raw_text)
        matches = self._index.get(normalized, [])
        candidates = [entry.as_candidate() for entry in matches]
        if len(candidates) == 1:
            candidate = candidates[0]
            return RegionRef(
                raw_text=raw_text,
                canonical_name=candidate.canonical_name,
                province_code=candidate.province_code,
                city_code=candidate.city_code,
                county_code=candidate.county_code,
                resolution=RegionResolution.RESOLVED,
                candidates=candidates,
            )
        if candidates:
            return RegionRef(
                raw_text=raw_text,
                resolution=RegionResolution.AMBIGUOUS,
                candidates=candidates,
            )
        return RegionRef(
            raw_text=raw_text,
            resolution=RegionResolution.UNRESOLVED,
        )

    def find_mentions(self, message: str) -> list[RegionMention]:
        compact_characters: list[str] = []
        source_positions: list[int] = []
        for index, character in enumerate(message):
            if character.isspace():
                continue
            compact_characters.append(character)
            source_positions.append(index)
        normalized = "".join(compact_characters)
        possible: list[tuple[int, int, str]] = []
        for alias in self._aliases:
            start = normalized.find(alias)
            while start >= 0:
                possible.append((start, start + len(alias), alias))
                start = normalized.find(alias, start + 1)

        selected: list[tuple[int, int, str]] = []
        for candidate in sorted(
            possible,
            key=lambda item: (item[0], -(item[1] - item[0])),
        ):
            start, end, _ = candidate
            if any(
                start < kept_end and end > kept_start
                for kept_start, kept_end, _ in selected
            ):
                continue
            selected.append(candidate)

        return [
            RegionMention(
                start=source_positions[start],
                end=source_positions[end - 1] + 1,
                raw_text=alias,
                ref=self.resolve(alias),
            )
            for start, end, alias in sorted(selected)
        ]

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(value.strip().split())


DEFAULT_REGION_ENTRIES = (
    RegionCatalogEntry(
        canonical_name="北京市",
        aliases=("北京",),
        province_code="110000",
        city_code="110100",
    ),
    RegionCatalogEntry(
        canonical_name="上海市",
        aliases=("上海",),
        province_code="310000",
        city_code="310100",
    ),
    RegionCatalogEntry(
        canonical_name="天津市",
        aliases=("天津",),
        province_code="120000",
        city_code="120100",
    ),
    RegionCatalogEntry(
        canonical_name="广州市",
        aliases=("广州",),
        province_code="440000",
        city_code="440100",
    ),
    RegionCatalogEntry(
        canonical_name="深圳市",
        aliases=("深圳",),
        province_code="440000",
        city_code="440300",
    ),
    RegionCatalogEntry(
        canonical_name="杭州市",
        aliases=("杭州",),
        province_code="330000",
        city_code="330100",
    ),
    RegionCatalogEntry(
        canonical_name="北京市朝阳区",
        aliases=("朝阳区", "北京朝阳"),
        province_code="110000",
        city_code="110100",
        county_code="110105",
    ),
    RegionCatalogEntry(
        canonical_name="长春市朝阳区",
        aliases=("朝阳区", "长春朝阳"),
        province_code="220000",
        city_code="220100",
        county_code="220104",
    ),
)


def create_demo_region_resolver() -> RegionResolver:
    return RegionResolver(
        DEFAULT_REGION_ENTRIES,
        catalog_version="demo-regions-v1",
    )
