from __future__ import annotations

from collections.abc import Mapping
import re
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    AwareDatetime,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from ..domain.agent_actions import RequiredInput
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.results import AgentResult, AgentResultStatus, SourceReference
from ..domain.product_price_execution import PriceSelectionInput
from .product_price_schemas import ProductPriceResponseData, PriceCandidateOption


MessageText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]
PublicResultType = Literal[
    "policy",
    "device_price",
    "tracking",
    "delivery_time",
    "postage",
    "product_price",
]


class AgentNextAction(StrEnum):
    COLLECT_SLOTS = "collect_slots"
    CLARIFY_INTENT = "clarify_intent"
    COMPLETE = "complete"
    HANDOFF = "handoff"
    FAILED = "failed"


class AgentPhase(StrEnum):
    NEW = "new"
    UNDERSTANDING = "understanding"
    CLARIFYING = "clarifying"
    COLLECTING = "collecting"
    READY = "ready"
    EXECUTING = "executing"
    VALIDATING = "validating"
    RECOVERING = "recovering"
    RESPONDING = "responding"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    HANDOFF = "handoff"
    FAILED = "failed"


class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None
    message: MessageText | None = None
    explicit_intent: Intent | None = None
    confirm_overwrite: bool = False
    stream: bool = False
    price_selection: PriceSelectionInput | None = None

    @model_validator(mode="after")
    def validate_user_input(self) -> "AgentMessageRequest":
        if (
            self.message is None
            and self.explicit_intent is None
            and self.price_selection is None
        ):
            raise ValueError("必须提供 message 或 explicit_intent")
        if self.explicit_intent is Intent.UNKNOWN:
            raise ValueError("explicit_intent 不能是 unknown")
        if self.price_selection is not None and (
            self.conversation_id is None
            or self.message is not None
            or self.explicit_intent is not None
            or self.confirm_overwrite
        ):
            raise ValueError("价格选择只能恢复已有会话，不能同时改题或覆盖条件")
        return self


class RequiredInputResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    type: Literal["string", "number", "region", "choice"]
    validation_hint: str = ""
    choices: list[str] = Field(default_factory=list)
    price_candidates: list[PriceCandidateOption] = Field(
        default_factory=list, max_length=20
    )

    @classmethod
    def from_domain(cls, value: RequiredInput) -> "RequiredInputResponse":
        return cls.model_validate(value.model_dump(mode="json"))


class AgentSourceResponse(BaseModel):
    """Allowlisted tracking observation, not raw domain/wire provenance."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["fake_gateway", "external_api", "unknown"]
    source_name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
    source_profile: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{0,128}$")
    ] = ""
    queried_at: AwareDatetime | None = None
    history_completeness: Literal["complete", "partial", "unknown"] = "unknown"

    @classmethod
    def from_tracking_source(cls, source: SourceReference) -> "AgentSourceResponse":
        known = source.source_type in {"fake_gateway", "external_api"} and bool(
            re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", source.source_name)
        )
        observed = source.queried_at
        if observed is not None and observed.utcoffset() is None:
            observed = None
        return cls(
            source_type=source.source_type if known else "unknown",
            source_name=source.source_name if known else "legacy-unknown",
            source_profile=source.source_profile if known else "",
            queried_at=observed,
            history_completeness=source.history_completeness if known else "unknown",
        )


PublicQuoteAmount = Annotated[
    str, StringConstraints(strict=True, pattern=r"^(0|[1-9][0-9]{0,8})\.[0-9]{2}$")
]
_PUBLIC_QUOTE_AMOUNT = TypeAdapter(PublicQuoteAmount)
_PUBLIC_QUOTE_TIME = TypeAdapter(AwareDatetime)


class PostageSourceResponse(BaseModel):
    """Quotation observation: deliberately no tracking history semantics."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["fake_gateway", "external_api"]
    source_name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
    source_profile: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    ]
    queried_at: AwareDatetime


class PostageFeeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "registration",
        "insurance",
        "declared_value",
        "inspection",
        "customs",
        "fuel",
        "return_receipt",
        "password_delivery",
        "printing",
        "handling",
    ]
    amount: PublicQuoteAmount
    included_in_amount: Literal["yes", "no", "unknown"]


class PostageQuoteBasisResponse(BaseModel):
    """Versioned public allowlist. No pricing identity, hashes or billing IDs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    amount_kind: Literal["total", "standard", "customer"]
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    product_code: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
    scope: Literal["domestic_actual_weight_no_extras"]
    is_estimate: Literal[True]
    fees: list[PostageFeeResponse] = Field(default_factory=list)
    source: PostageSourceResponse

    @model_validator(mode="after")
    def unique_fees(self) -> "PostageQuoteBasisResponse":
        if len({fee.kind for fee in self.fees}) != len(self.fees):
            raise ValueError("报价费用项不能重复")
        return self


class AgentResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: PublicResultType
    status: AgentResultStatus
    data: dict[str, Any] | None = None
    reason_code: str = ""
    provenance: list[AgentSourceResponse] = Field(default_factory=list)
    quote_basis: PostageQuoteBasisResponse | None = None

    @model_validator(mode="after")
    def validate_quote_basis(self) -> "AgentResultResponse":
        if self.type == "product_price":
            if self.status in {AgentResultStatus.SUCCESS, AgentResultStatus.PARTIAL}:
                ProductPriceResponseData.model_validate(self.data)
            elif self.data is not None:
                raise ValueError("非报价结果不能包含商品价格数据")
        basis = self.quote_basis
        if basis is not None:
            if self.type != "postage" or self.status != "success" or self.data is None:
                raise ValueError("报价依据仅能出现在成功资费结果中")
            if (self.data.get("currency"), self.data.get("product_code")) != (
                basis.currency,
                basis.product_code,
            ):
                raise ValueError("报价依据必须与资费数据一致")
            _PUBLIC_QUOTE_AMOUNT.validate_python(self.data.get("amount"))
            if (
                _PUBLIC_QUOTE_TIME.validate_python(self.data.get("queried_at"))
                != basis.source.queried_at
            ):
                raise ValueError("报价依据观察时间必须与数据一致")
        return self

    @classmethod
    def from_domain(cls, value: AgentResult) -> "AgentResultResponse":
        if value.intent is Intent.UNKNOWN:
            raise ValueError("AgentResult 不能使用 unknown 意图")
        data = None
        if value.intent is Intent.PRODUCT_PRICE and value.data is not None:
            data = ProductPriceResponseData.from_domain(value.data).model_dump(
                mode="json"
            )
        elif value.data is not None:
            data = value.data.model_dump(mode="json")
        quote_basis = None
        if value.intent is Intent.POSTAGE and data is not None:
            internal_basis = data.pop("quote_basis", None)
            if internal_basis is not None and value.status is AgentResultStatus.SUCCESS:
                data["amount"] = format(value.data.amount, ".2f")
                if len(value.provenance) != 1:
                    raise ValueError("报价缺少唯一观察来源")
                context = internal_basis["context"]
                source = value.provenance[0]
                quote_basis = PostageQuoteBasisResponse(
                    amount_kind=context["amount_kind"],
                    currency=context["currency"],
                    product_code=context["product_code"],
                    scope=context["scope"],
                    is_estimate=internal_basis["is_estimate"],
                    fees=[
                        PostageFeeResponse(
                            kind=fee.kind,
                            amount=format(fee.amount, ".2f"),
                            included_in_amount=fee.included_in_amount,
                        )
                        for fee in value.data.quote_basis.fees
                    ],
                    source=PostageSourceResponse(
                        source_type=source.source_type,
                        source_name=source.source_name,
                        source_profile=source.source_profile,
                        queried_at=source.queried_at,
                    ),
                )
        return cls(
            type=value.intent.value,
            status=value.status,
            data=data,
            reason_code=value.reason_code,
            quote_basis=quote_basis,
            provenance=[
                AgentSourceResponse.from_tracking_source(source)
                for source in value.provenance
            ]
            if value.intent is Intent.TRACKING
            else [],
        )


class AgentFailureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: FailureCategory
    code: str
    retryable: bool
    retry_after_seconds: float | None = None

    @classmethod
    def from_domain(cls, value: AgentFailure) -> "AgentFailureResponse":
        return cls(
            category=value.category,
            code=value.code,
            retryable=value.retryable,
            retry_after_seconds=value.retry_after_seconds,
        )


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    conversation_id: UUID
    turn_id: UUID
    phase: AgentPhase
    intent: Intent | None = None
    reply: str
    next_action: AgentNextAction
    required_inputs: list[RequiredInputResponse]
    result: AgentResultResponse | None = None
    failure: AgentFailureResponse | None = None
    warnings: list[str]

    @model_validator(mode="after")
    def validate_public_state(self) -> "AgentResponse":
        if self.phase is AgentPhase.FAILED and self.failure is None:
            raise ValueError("failed 响应必须包含 failure")
        if self.phase is not AgentPhase.FAILED and self.failure is not None:
            raise ValueError("只有 failed 响应可以包含 failure")
        if self.result is not None:
            if self.phase is not AgentPhase.COMPLETED:
                raise ValueError("只有 completed 响应可以包含 result")
            if self.intent is None or self.result.type != self.intent.value:
                raise ValueError("result.type 必须与响应 intent 一致")
        if self.phase is AgentPhase.WAITING_USER and not self.required_inputs:
            raise ValueError("waiting_user 响应必须包含 required_inputs")
        return self

    @classmethod
    def from_runtime(
        cls,
        *,
        request_id: str,
        output: Mapping[str, Any],
    ) -> "AgentResponse":
        phase = AgentPhase(str(output.get("phase", "")))
        required_inputs = [
            RequiredInput.model_validate(item)
            for item in output.get("required_inputs", [])
        ]
        raw_intent = output.get("active_intent")
        intent = Intent(str(raw_intent)) if raw_intent else None
        raw_result = output.get("result")
        result = (
            AgentResultResponse.from_domain(AgentResult.model_validate(raw_result))
            if raw_result is not None
            else None
        )
        raw_failure = output.get("failure")
        failure = (
            AgentFailureResponse.from_domain(AgentFailure.model_validate(raw_failure))
            if raw_failure is not None
            else None
        )
        return cls(
            request_id=request_id,
            conversation_id=UUID(str(output.get("conversation_id", ""))),
            turn_id=UUID(str(output.get("turn_id", ""))),
            phase=phase,
            intent=intent,
            reply=str(output.get("reply", "")),
            next_action=_next_action(phase, required_inputs),
            required_inputs=[
                RequiredInputResponse.from_domain(item) for item in required_inputs
            ],
            result=result,
            failure=failure,
            warnings=[str(item) for item in output.get("warnings", [])],
        )


class AgentCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: PublicResultType
    display_name: str
    available: bool
    capability_version: str | None = None
    required_inputs: list[RequiredInputResponse]


AgentReadinessState = Literal[
    "ready",
    "not_ready",
    "degraded",
    "starting",
    "disabled",
]


class AgentHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "not_ready"]
    service: Literal["spb-assistant-agent-v2"] = "spb-assistant-agent-v2"
    version: str
    phase: Literal[4] = 4
    checks: dict[str, AgentReadinessState]


class AgentError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str
    category: FailureCategory | None = None
    retryable: bool = False
    retry_after_seconds: float | None = Field(default=None, ge=0)


class AgentErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: AgentError


class AgentStreamStatusEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    stage: Literal["accepted"] = "accepted"
    message: str


class AgentStreamStateEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    conversation_id: UUID
    turn_id: UUID
    phase: AgentPhase
    intent: Intent | None = None
    next_action: AgentNextAction


class AgentStreamInputRequiredEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    conversation_id: UUID
    turn_id: UUID
    required_inputs: list[RequiredInputResponse]


class AgentStreamResultEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    conversation_id: UUID
    turn_id: UUID
    result: AgentResultResponse | None = None
    failure: AgentFailureResponse | None = None
    warnings: list[str] = Field(default_factory=list)


class AgentStreamDeltaEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    conversation_id: UUID
    turn_id: UUID
    content: str


class AgentStreamDoneEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    response: AgentResponse


class AgentStreamErrorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    code: str
    message: str
    http_status: int = Field(ge=400, le=599)
    category: FailureCategory | None = None
    retryable: bool = False
    retry_after_seconds: float | None = Field(default=None, ge=0)


def _next_action(
    phase: AgentPhase,
    required_inputs: list[RequiredInput],
) -> AgentNextAction:
    if phase is AgentPhase.WAITING_USER:
        if any(item.name == "intent" for item in required_inputs):
            return AgentNextAction.CLARIFY_INTENT
        return AgentNextAction.COLLECT_SLOTS
    if phase is AgentPhase.COMPLETED:
        return AgentNextAction.COMPLETE
    if phase is AgentPhase.HANDOFF:
        return AgentNextAction.HANDOFF
    if phase is AgentPhase.FAILED:
        return AgentNextAction.FAILED
    raise ValueError(f"不可投影未停止的 Agent phase: {phase.value}")
