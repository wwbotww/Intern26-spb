from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SearchQuery:
    text: str
    top_k: int = 8


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    document_id: str
    title: str
    text: str
    source_url: str
    section_path: str
    score: float


@dataclass(frozen=True)
class ChatEvent:
    event: Literal["metadata", "delta", "usage", "done", "error"]
    data: dict
