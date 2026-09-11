from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID
from .product_price_contract import ProductPriceResponseData, PriceCandidateOption

from pydantic import (
    BaseModel,
    AwareDatetime,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
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
AgentIntent = Literal[
    "product_price",
    "policy",
    "device_price",
    "tracking",
    "delivery_time",
    "postage",
    "unknown",
]
AgentControl = Literal["none", "cancel", "restart"]
AgentPublicIntent = Literal[
    "product_price",
    "policy",
    "device_price",
    "tracking",
    "delivery_time",
    "postage",
]
AgentTerminalPhase = Literal[
    "waiting_user",
    "completed",
    "handoff",
    "failed",
]
AgentNextAction = Literal[
    "collect_slots",
    "clarify_intent",
    "complete",
    "handoff",
    "failed",
]
AgentResultStatus = Literal[
    "success",
    "partial",
    "need_more_info",
    "no_match",
    "failed",
]
AgentFailureCategory = Literal[
    "missing_input",
    "ambiguous_intent",
    "invalid_input",
    "no_match",
    "upstream_timeout",
    "upstream_rate_limited",
    "upstream_unavailable",
    "contract_violation",
    "state_conflict",
    "persistence_unavailable",
    "state_schema_incompatible",
    "loop_budget_exceeded",
    "internal_error",
]
_AGENT_PUBLIC_INTENTS = {
    "product_price",
    "policy",
    "device_price",
    "tracking",
    "delivery_time",
    "postage",
}


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


class AgentUnderstandingTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: NonEmptyText
    explicit_intent: AgentIntent | None = None
    expected_intent: AgentIntent
    expected_missing_slots: list[NonEmptyText] = Field(
        default_factory=list
    )
    expected_slot_values: dict[str, Any] = Field(default_factory=dict)
    expected_ambiguities: list[NonEmptyText] = Field(default_factory=list)
    expected_multi_intent: bool = False
    expected_control: AgentControl = "none"


class AgentUnderstandingEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NonEmptyText
    category: NonEmptyText
    turns: list[AgentUnderstandingTurn] = Field(min_length=1)
    split: DatasetSplit = "calibration"
    tags: list[NonEmptyText] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def deduplicate_labels(self) -> "AgentUnderstandingEvalCase":
        self.tags = list(dict.fromkeys(self.tags))
        for turn in self.turns:
            turn.expected_missing_slots = list(
                dict.fromkeys(turn.expected_missing_slots)
            )
            turn.expected_ambiguities = list(
                dict.fromkeys(turn.expected_ambiguities)
            )
        return self


class PriceSelectionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_token: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]


class AgentEvalTurn(BaseModel):
    """One public V2 message and its black-box expectations."""

    model_config = ConfigDict(extra="forbid")

    message: NonEmptyText | None = None
    explicit_intent: AgentPublicIntent | None = None
    confirm_overwrite: bool = False
    price_selection: PriceSelectionObservation | None = None
    expected_phase: AgentTerminalPhase
    expected_intent: AgentIntent | None = None
    expected_next_action: AgentNextAction
    expected_required_inputs: list[NonEmptyText] = Field(
        default_factory=list
    )
    expected_result_status: AgentResultStatus | None = None
    expected_result_values: dict[str, Any] = Field(default_factory=dict)
    expected_quote_basis_values: dict[str, Any] = Field(default_factory=dict)
    expected_failure_category: AgentFailureCategory | None = None
    expected_failure_code: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_public_expectations(self) -> "AgentEvalTurn":
        if self.message is None and self.explicit_intent is None and self.price_selection is None:
            raise ValueError("Agent turn 必须提供 message 或 explicit_intent")
        if self.price_selection is not None and (self.message is not None or self.explicit_intent is not None or self.confirm_overwrite):
            raise ValueError("候选选择不能与新条件混合提交")
        self.expected_required_inputs = list(
            dict.fromkeys(self.expected_required_inputs)
        )
        expected_action = {
            "completed": "complete",
            "handoff": "handoff",
            "failed": "failed",
        }.get(self.expected_phase)
        if expected_action and self.expected_next_action != expected_action:
            raise ValueError(
                "expected_phase 与 expected_next_action 不一致"
            )
        if self.expected_phase == "waiting_user":
            if self.expected_next_action not in {
                "collect_slots",
                "clarify_intent",
            }:
                raise ValueError("waiting_user 必须对应澄清动作")
            if not self.expected_required_inputs:
                raise ValueError("waiting_user 必须声明 required inputs")
        elif self.expected_required_inputs:
            raise ValueError("非 waiting_user 不能声明 required inputs")
        if (
            self.expected_result_status is not None
            and self.expected_phase != "completed"
        ):
            raise ValueError("只有 completed turn 可以期待 result")
        if (
            (self.expected_result_values or self.expected_quote_basis_values)
            and self.expected_result_status is None
        ):
            raise ValueError("result value 断言必须同时声明 result status")
        if self.expected_failure_category is not None:
            if self.expected_phase != "failed":
                raise ValueError("只有 failed turn 可以期待 failure")
        elif self.expected_failure_code is not None:
            raise ValueError("failure code 断言必须同时声明 failure category")
        return self


class AgentEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NonEmptyText
    category: NonEmptyText
    turns: list[AgentEvalTurn] = Field(min_length=1, max_length=10)
    split: DatasetSplit | Literal["development"] = "calibration"
    tags: list[NonEmptyText] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def deduplicate_agent_labels(self) -> "AgentEvalCase":
        self.tags = list(dict.fromkeys(self.tags))
        return self


class AgentRequiredInputObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    type: Literal["string", "number", "region", "choice"]
    validation_hint: str = ""
    choices: list[str] = Field(default_factory=list)
    price_candidates: list[PriceCandidateOption] = Field(default_factory=list, max_length=20)


class AgentSourceObservation(BaseModel):
    """Independent mirror of the T3 public allowlist, not server domain imports."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["fake_gateway", "external_api", "unknown"]
    source_name: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    ]
    source_profile: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{0,128}$")
    ] = ""
    queried_at: AwareDatetime | None = None
    history_completeness: Literal["complete", "partial", "unknown"] = "unknown"


PublicQuoteAmount = Annotated[
    str, StringConstraints(strict=True, pattern=r"^(0|[1-9][0-9]{0,8})\.[0-9]{2}$")
]
_PUBLIC_QUOTE_AMOUNT = TypeAdapter(PublicQuoteAmount)
_PUBLIC_QUOTE_TIME = TypeAdapter(AwareDatetime)


class PostageSourceObservation(BaseModel):
    """Quotation observation: deliberately no tracking history semantics."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["fake_gateway", "external_api"]
    source_name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
    source_profile: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
    queried_at: AwareDatetime


class PostageFeeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "registration", "insurance", "declared_value", "inspection", "customs",
        "fuel", "return_receipt", "password_delivery", "printing", "handling",
    ]
    amount: PublicQuoteAmount
    included_in_amount: Literal["yes", "no", "unknown"]


class PostageQuoteBasisObservation(BaseModel):
    """Versioned public allowlist. No pricing identity, hashes or billing IDs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    amount_kind: Literal["total", "standard", "customer"]
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    product_code: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
    scope: Literal["domestic_actual_weight_no_extras"]
    is_estimate: Literal[True]
    fees: list[PostageFeeObservation] = Field(default_factory=list)
    source: PostageSourceObservation

    @model_validator(mode="after")
    def unique_fees(self) -> "PostageQuoteBasisObservation":
        if len({fee.kind for fee in self.fees}) != len(self.fees):
            raise ValueError("报价费用项不能重复")
        return self


class AgentResultObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AgentPublicIntent
    status: AgentResultStatus
    data: dict[str, Any] | None = None
    reason_code: str = ""
    provenance: list[AgentSourceObservation] = Field(default_factory=list)
    quote_basis: PostageQuoteBasisObservation | None = None

    @model_validator(mode="after")
    def validate_quote_basis(self) -> "AgentResultObservation":
        if self.type == "product_price":
            if self.status in {"success", "partial"}:
                ProductPriceResponseData.model_validate(self.data)
            elif self.data is not None:
                raise ValueError("非报价结果不能包含商品价格数据")
        basis = self.quote_basis
        if basis is not None:
            if self.type != "postage" or self.status != "success" or self.data is None:
                raise ValueError("报价依据仅能出现在成功资费结果中")
            if (self.data.get("currency"), self.data.get("product_code")) != (
                basis.currency, basis.product_code,
            ):
                raise ValueError("报价依据必须与资费数据一致")
            _PUBLIC_QUOTE_AMOUNT.validate_python(self.data.get("amount"))
            if _PUBLIC_QUOTE_TIME.validate_python(self.data.get("queried_at")) != basis.source.queried_at:
                raise ValueError("报价依据观察时间必须与数据一致")
        return self



class AgentFailureObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: AgentFailureCategory
    code: str
    retryable: bool
    retry_after_seconds: float | None = None


class AgentPublicResponse(BaseModel):
    """Strict mirror of the stable non-streaming V2 response."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    conversation_id: UUID
    turn_id: UUID
    phase: AgentTerminalPhase
    intent: AgentIntent | None = None
    reply: str
    next_action: AgentNextAction
    required_inputs: list[AgentRequiredInputObservation]
    result: AgentResultObservation | None = None
    failure: AgentFailureObservation | None = None
    warnings: list[str]

    @model_validator(mode="after")
    def validate_terminal_contract(self) -> "AgentPublicResponse":
        if self.phase == "waiting_user" and not self.required_inputs:
            raise ValueError("waiting_user 响应必须包含 required inputs")
        if self.phase != "waiting_user" and self.required_inputs:
            raise ValueError("非 waiting_user 响应不能包含 required inputs")
        if self.phase == "failed" and self.failure is None:
            raise ValueError("failed 响应必须包含 failure")
        if self.phase != "failed" and self.failure is not None:
            raise ValueError("非 failed 响应不能包含 failure")
        if self.result is not None:
            if self.phase != "completed":
                raise ValueError("只有 completed 响应可以包含 result")
            if self.intent not in _AGENT_PUBLIC_INTENTS:
                raise ValueError("result 响应必须包含公开 intent")
            if self.result.type != self.intent:
                raise ValueError("result.type 必须与 intent 一致")
        return self


class AgentTurnObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "error"]
    client_elapsed_ms: float
    request_id: str = ""
    conversation_id: str = ""
    turn_id: str = ""
    phase: AgentTerminalPhase | None = None
    intent: AgentIntent | None = None
    reply: str = ""
    next_action: AgentNextAction | None = None
    required_inputs: list[AgentRequiredInputObservation] = Field(
        default_factory=list
    )
    result: AgentResultObservation | None = None
    failure: AgentFailureObservation | None = None
    warnings: list[str] = Field(default_factory=list)
    http_status: int | None = None
    error_code: str = ""
    error_category: str = ""
    retryable: bool = False
    error: str = ""


class AgentTurnResult(BaseModel):
    turn_index: int = Field(ge=1)
    expected: AgentEvalTurn
    observation: AgentTurnObservation


class AgentCaseResult(BaseModel):
    case: AgentEvalCase
    turns: list[AgentTurnResult] = Field(default_factory=list)


class AgentEvalThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_case_pass_rate: float = Field(default=1.0, ge=0, le=1)
    min_intent_accuracy: float = Field(default=0.95, ge=0, le=1)
    min_required_input_accuracy: float = Field(default=0.95, ge=0, le=1)
    max_wrong_tool_rate: float = Field(default=0.0, ge=0, le=1)
    min_task_completion_rate: float = Field(default=0.90, ge=0, le=1)
    min_recovery_rate: float = Field(default=0.90, ge=0, le=1)
    max_api_error_rate: float = Field(default=0.0, ge=0, le=1)


class AgentRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    base_url: str
    dataset: str
    dataset_sha256: str = ""
    concurrency: int
    timeout_seconds: float
    thresholds: AgentEvalThresholds = Field(
        default_factory=AgentEvalThresholds
    )
    git_commit: str = "unknown"


class AgentRunReport(BaseModel):
    generated_at: str
    config: AgentRunConfig
    service: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any]
    results: list[AgentCaseResult]


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


class AgentTurnTransition(BaseModel):
    case_id: str
    category: str
    turn_index: int = Field(ge=1)
    change: Literal["turn_regression", "turn_improvement"]
    baseline: str
    experiment: str


class AgentComparisonReport(BaseModel):
    generated_at: str
    baseline_report: str
    experiment_report: str
    baseline_label: str
    experiment_label: str
    dataset_sha256: str
    sample_coverage: dict[str, Any]
    quality_gate: dict[str, bool]
    metrics: list[MetricDelta]
    regressions: list[AgentTurnTransition]
    improvements: list[AgentTurnTransition]
