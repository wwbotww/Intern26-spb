from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    model_validator,
)

from ..domain.models import SearchFilters, SearchHit, SearchQuery
from ..services.chat import Citation
from ..settings import ApiSettings


FilterValue = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
QueryText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    phase: int
    checks: dict[str, str]


class SearchFiltersRequest(BaseModel):
    document_types: list[FilterValue] = Field(
        default_factory=list,
        max_length=10,
    )
    validity_statuses: list[FilterValue] = Field(
        default_factory=list,
        max_length=10,
    )
    source_orgs: list[FilterValue] = Field(
        default_factory=list,
        max_length=20,
    )
    published_from: date | None = None
    published_through: date | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> "SearchFiltersRequest":
        if (
            self.published_from
            and self.published_through
            and self.published_from > self.published_through
        ):
            raise ValueError("published_from 不能晚于 published_through")
        return self

    def to_domain(self) -> SearchFilters:
        return SearchFilters(
            document_types=tuple(dict.fromkeys(self.document_types)),
            validity_statuses=tuple(
                dict.fromkeys(self.validity_statuses)
            ),
            source_orgs=tuple(dict.fromkeys(self.source_orgs)),
            published_from=(
                self.published_from.isoformat()
                if self.published_from
                else None
            ),
            published_through=(
                self.published_through.isoformat()
                if self.published_through
                else None
            ),
        )


class SearchRequest(BaseModel):
    query: QueryText
    top_k: int | None = Field(default=None, ge=1, le=100)
    candidate_k: int | None = Field(default=None, ge=1, le=500)
    filters: SearchFiltersRequest = Field(
        default_factory=SearchFiltersRequest
    )

    def to_domain(self, settings: ApiSettings) -> SearchQuery:
        top_k = self.top_k or settings.search_default_top_k
        if top_k > settings.search_max_top_k:
            raise ValueError(
                f"top_k 不能超过服务上限 {settings.search_max_top_k}"
            )
        candidate_k = self.candidate_k or max(
            settings.search_candidate_k,
            top_k,
        )
        if candidate_k < top_k:
            raise ValueError("candidate_k 不能小于 top_k")
        if candidate_k > settings.search_max_candidate_k:
            raise ValueError(
                "candidate_k 不能超过服务上限 "
                f"{settings.search_max_candidate_k}"
            )
        return SearchQuery(
            text=self.query,
            top_k=top_k,
            candidate_k=candidate_k,
            filters=self.filters.to_domain(),
        )


class SearchResult(BaseModel):
    rank: int
    score: float
    rerank_score: float | None = None
    chunk_id: str
    document_id: str
    parent_document_id: str
    title: str
    text: str
    source_url: str
    document_type: str
    published_at: str
    document_no: str
    source_org: str
    validity_status: str
    section_path: str
    chunk_index: int

    @classmethod
    def from_domain(cls, hit: SearchHit, rank: int) -> "SearchResult":
        return cls(rank=rank, **hit.__dict__)


class SearchResponse(BaseModel):
    query: str
    mode: Literal[
        "hybrid_rrf",
        "hybrid_rrf_rerank",
    ] = "hybrid_rrf"
    count: int
    elapsed_ms: float
    results: list[SearchResult]


class ChatRequest(BaseModel):
    question: QueryText
    stream: bool = True
    top_k: int | None = Field(default=None, ge=1, le=100)
    candidate_k: int | None = Field(default=None, ge=1, le=500)
    filters: SearchFiltersRequest = Field(
        default_factory=SearchFiltersRequest
    )

    def to_search_query(self, settings: ApiSettings) -> SearchQuery:
        search = SearchRequest(
            query=self.question,
            top_k=self.top_k,
            candidate_k=self.candidate_k,
            filters=self.filters,
        )
        return search.to_domain(settings)


class ChatCitation(BaseModel):
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
    rerank_score: float | None = None
    excerpt: str

    @classmethod
    def from_domain(cls, citation: Citation) -> "ChatCitation":
        return cls(**citation.to_dict())


class ChatResponse(BaseModel):
    request_id: str
    model: str
    answer: str
    citations: list[ChatCitation]
    usage: dict[str, Any] = Field(default_factory=dict)
    finish_reason: str
