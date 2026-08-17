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
Difficulty = Literal["easy", "medium", "hard"]
DatasetSplit = Literal["calibration", "holdout"]
AssistantQueryMode = Literal["policy", "device_price"]
AssistantExpectedOutcome = Literal[
    "answer",
    "no_match",
    "need_more_info",
]


class EvalCase(BaseModel):
    id: NonEmptyText
    category: NonEmptyText
    question: NonEmptyText
    expected_outcome: ExpectedOutcome
    gold_document_ids: list[NonEmptyText] = Field(default_factory=list)
    gold_source_urls: list[NonEmptyText] = Field(default_factory=list)
    required_facts: list[list[NonEmptyText]] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    difficulty: Difficulty = "medium"
    split: DatasetSplit = "calibration"
    source_type: str = ""
    tags: list[NonEmptyText] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def validate_labels(self) -> "EvalCase":
        self.gold_document_ids = list(
            dict.fromkeys(self.gold_document_ids)
        )
        self.gold_source_urls = list(
            dict.fromkeys(self.gold_source_urls)
        )
        self.tags = list(dict.fromkeys(self.tags))
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


class AssistantEvalCase(BaseModel):
    id: NonEmptyText
    category: NonEmptyText
    mode: AssistantQueryMode
    question: NonEmptyText
    expected_outcome: AssistantExpectedOutcome
    expected_finish_reasons: list[NonEmptyText] = Field(
        default_factory=list
    )
    expected_reason_codes: list[NonEmptyText] = Field(
        default_factory=list
    )
    expected_missing_fields: list[NonEmptyText] = Field(
        default_factory=list
    )
    expected_product_ids: list[NonEmptyText] = Field(
        default_factory=list
    )
    expected_sku_ids: list[NonEmptyText] = Field(default_factory=list)
    min_evidence_count: int | None = Field(default=None, ge=0, le=100)
    tags: list[NonEmptyText] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def validate_expectations(self) -> "AssistantEvalCase":
        for field_name in (
            "expected_finish_reasons",
            "expected_reason_codes",
            "expected_missing_fields",
            "expected_product_ids",
            "expected_sku_ids",
            "tags",
        ):
            setattr(
                self,
                field_name,
                list(dict.fromkeys(getattr(self, field_name))),
            )
        default_finish_reasons = {
            "answer": ["stop"],
            "no_match": ["no_match"],
            "need_more_info": ["insufficient_information"],
        }
        if not self.expected_finish_reasons:
            self.expected_finish_reasons = default_finish_reasons[
                self.expected_outcome
            ]
        expected_minimum = 1 if self.expected_outcome == "answer" else 0
        if self.min_evidence_count is None:
            self.min_evidence_count = expected_minimum
        if (
            self.expected_outcome != "answer"
            and self.min_evidence_count != 0
        ):
            raise ValueError("非回答样本的 min_evidence_count 必须为 0")
        if (
            self.mode == "policy"
            and (self.expected_product_ids or self.expected_sku_ids)
        ):
            raise ValueError("政策样本不能设置设备产品或 SKU Gold")
        return self


class AssistantEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    evidence_id: str
    type: AssistantQueryMode
    title: str = ""
    source_url: str = ""
    excerpt: str = ""
    chunk_id: str = ""
    document_id: str = ""
    brand: str = ""
    model: str = ""
    specification: str = ""
    price: str = ""
    currency: str = ""
    source: str = ""
    observed_at: str = ""
    official_product_id: str = ""
    official_sku_id: str = ""
    match_score: float = 0.0


class AssistantChatObservation(BaseModel):
    status: Literal["ok", "error"]
    client_elapsed_ms: float
    request_id: str = ""
    mode: str = ""
    answer: str = ""
    evidence: list[AssistantEvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    used_tool: str = ""
    finish_reason: str = ""
    reason_code: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class AssistantCaseResult(BaseModel):
    case: AssistantEvalCase
    chat: AssistantChatObservation


class AssistantRunConfig(BaseModel):
    label: str
    base_url: str
    dataset: str
    concurrency: int
    timeout_seconds: float
    git_commit: str = "unknown"


class AssistantRunReport(BaseModel):
    generated_at: str
    config: AssistantRunConfig
    service: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any]
    results: list[AssistantCaseResult]


class ThresholdPoint(BaseModel):
    threshold: float = Field(ge=0, le=1)
    answerable_count: int
    unanswerable_count: int
    false_reject_count: int
    false_reject_rate: float | None
    false_accept_count: int
    false_accept_rate: float | None
    gold_survival_count: int
    gold_survival_rate: float | None
    accepted_query_rate: float | None
    constraints_met: bool


class ThresholdScanReport(BaseModel):
    source_report: str
    generated_at: str
    constraints: dict[str, float]
    coverage: dict[str, int]
    recommended_threshold: float | None
    recommendation_constraints_met: bool
    recommendation_reason: str
    points: list[ThresholdPoint]


class MetricDelta(BaseModel):
    metric: str
    baseline: float | int | None
    experiment: float | int | None
    delta: float | int | None
    direction: Literal["higher", "lower"]
    improved: bool | None


class CaseTransition(BaseModel):
    case_id: str
    category: str
    change: Literal[
        "gate_regression",
        "gate_improvement",
        "retrieval_regression",
        "retrieval_improvement",
        "citation_regression",
        "citation_improvement",
    ]
    baseline: str
    experiment: str


class ComparisonReport(BaseModel):
    generated_at: str
    baseline_report: str
    experiment_report: str
    baseline_label: str
    experiment_label: str
    sample_coverage: dict[str, Any]
    metrics: list[MetricDelta]
    regressions: list[CaseTransition]
    improvements: list[CaseTransition]
