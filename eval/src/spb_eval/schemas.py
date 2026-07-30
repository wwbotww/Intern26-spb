from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
ExpectedOutcome = Literal["answer", "reject"]
EvalMode = Literal["retrieve", "chat", "all"]


class EvalCase(BaseModel):
    id: NonEmptyText
    category: NonEmptyText
    question: NonEmptyText
    expected_outcome: ExpectedOutcome
    gold_document_ids: list[NonEmptyText] = Field(default_factory=list)
    gold_source_urls: list[NonEmptyText] = Field(default_factory=list)
    required_facts: list[list[NonEmptyText]] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    @model_validator(mode="after")
    def validate_labels(self) -> "EvalCase":
        self.gold_document_ids = list(
            dict.fromkeys(self.gold_document_ids)
        )
        self.gold_source_urls = list(
            dict.fromkeys(self.gold_source_urls)
        )
        for group in self.required_facts:
            if not group:
                raise ValueError("required_facts 不能包含空分组")
        if (
            self.expected_outcome == "answer"
            and not self.gold_document_ids
            and not self.gold_source_urls
        ):
            raise ValueError(
                "可回答样本必须提供 gold_document_ids "
                "或 gold_source_urls"
            )
        return self


class RetrievedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rank: int
    document_id: str
    source_url: str
    title: str = ""
    score: float = 0.0
    rerank_score: float | None = None


class RetrieveObservation(BaseModel):
    status: Literal["ok", "error"]
    client_elapsed_ms: float
    server_elapsed_ms: float | None = None
    mode: str = ""
    results: list[RetrievedItem] = Field(default_factory=list)
    error: str = ""


class CitationItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_id: str
    source_url: str
    title: str = ""
    rerank_score: float | None = None


class ChatObservation(BaseModel):
    status: Literal["ok", "error"]
    client_elapsed_ms: float
    finish_reason: str = ""
    answer: str = ""
    citations: list[CitationItem] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class CaseResult(BaseModel):
    case: EvalCase
    retrieval: RetrieveObservation | None = None
    chat: ChatObservation | None = None


class RunConfig(BaseModel):
    label: str
    base_url: str
    dataset: str
    mode: EvalMode
    top_k: int
    candidate_k: int
    concurrency: int
    timeout_seconds: float
    git_commit: str = "unknown"


class RunReport(BaseModel):
    generated_at: str
    config: RunConfig
    service: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any]
    results: list[CaseResult]
