from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SearchFilters:
    document_types: tuple[str, ...] = ()
    validity_statuses: tuple[str, ...] = ()
    source_orgs: tuple[str, ...] = ()
    published_from: str | None = None
    published_through: str | None = None


@dataclass(frozen=True)
class SearchQuery:
    text: str
    top_k: int = 8
    candidate_k: int = 40
    filters: SearchFilters = SearchFilters()


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    document_id: str
    title: str
    text: str
    source_url: str
    section_path: str
    score: float
    parent_document_id: str = ""
    document_type: str = ""
    published_at: str = ""
    document_no: str = ""
    source_org: str = ""
    validity_status: str = "unknown"
    chunk_index: int = 0


@dataclass(frozen=True)
class ChatEvent:
    event: Literal["metadata", "delta", "usage", "done", "error"]
    data: dict
