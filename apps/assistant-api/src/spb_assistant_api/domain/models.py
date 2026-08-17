from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeAlias


class QueryMode(StrEnum):
    POLICY = "policy"
    DEVICE_PRICE = "device_price"


class ToolStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    NEED_MORE_INFO = "need_more_info"
    NO_MATCH = "no_match"
    ERROR = "error"


class EvidenceType(StrEnum):
    POLICY = "policy"
    DEVICE_PRICE = "device_price"


@dataclass(frozen=True, slots=True)
class PolicyEvidence:
    evidence_id: str
    title: str
    source_url: str
    excerpt: str
    document_no: str = ""
    published_at: str = ""
    source_org: str = ""
    section_path: str = ""
    chunk_id: str = ""
    document_id: str = ""
    score: float = 0.0
    rerank_score: float | None = None
    type: EvidenceType = field(
        default=EvidenceType.POLICY,
        init=False,
    )


@dataclass(frozen=True, slots=True)
class DevicePriceEvidence:
    evidence_id: str
    title: str
    brand: str
    model: str
    specification: str
    price: str
    currency: str
    source: str
    observed_at: str
    availability: str = ""
    source_url: str = ""
    original_price: str | None = None
    original_price_type: str = ""
    official_product_id: str = ""
    official_sku_id: str = ""
    match_score: float = 0.0
    type: EvidenceType = field(
        default=EvidenceType.DEVICE_PRICE,
        init=False,
    )


Evidence: TypeAlias = PolicyEvidence | DevicePriceEvidence


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool: str
    status: ToolStatus
    answer: str
    evidence: tuple[Evidence, ...] = ()
    warnings: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)
    reason_code: str = ""

    def __post_init__(self) -> None:
        if not self.tool.strip():
            raise ValueError("tool 不能为空")
        if self.status is ToolStatus.SUCCESS and not self.answer.strip():
            raise ValueError("成功结果必须包含 answer")
