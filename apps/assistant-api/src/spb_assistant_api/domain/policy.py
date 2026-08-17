from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PolicyCitation:
    index: int
    chunk_id: str
    document_id: str
    title: str
    source_url: str
    document_no: str
    published_at: str
    source_org: str
    section_path: str
    score: float
    rerank_score: float | None
    excerpt: str


@dataclass(frozen=True, slots=True)
class PolicyQueryResult:
    answer: str
    citations: tuple[PolicyCitation, ...]
    finish_reason: str
    usage: dict[str, Any] = field(default_factory=dict)
