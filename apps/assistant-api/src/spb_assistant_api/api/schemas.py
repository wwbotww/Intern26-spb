from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from ..domain.models import (
    DevicePriceEvidence,
    Evidence,
    PolicyEvidence,
    QueryMode,
    ToolResult,
)


QuestionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: QueryMode
    question: QuestionText
    stream: bool = True


class PolicyEvidenceResponse(BaseModel):
    evidence_id: str
    type: Literal["policy"] = "policy"
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

    @classmethod
    def from_domain(
        cls,
        evidence: PolicyEvidence,
    ) -> "PolicyEvidenceResponse":
        return cls(
            evidence_id=evidence.evidence_id,
            title=evidence.title,
            source_url=evidence.source_url,
            excerpt=evidence.excerpt,
            document_no=evidence.document_no,
            published_at=evidence.published_at,
            source_org=evidence.source_org,
            section_path=evidence.section_path,
            chunk_id=evidence.chunk_id,
            document_id=evidence.document_id,
            score=evidence.score,
            rerank_score=evidence.rerank_score,
        )


class DevicePriceEvidenceResponse(BaseModel):
    evidence_id: str
    type: Literal["device_price"] = "device_price"
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

    @classmethod
    def from_domain(
        cls,
        evidence: DevicePriceEvidence,
    ) -> "DevicePriceEvidenceResponse":
        return cls(
            evidence_id=evidence.evidence_id,
            title=evidence.title,
            brand=evidence.brand,
            model=evidence.model,
            specification=evidence.specification,
            price=evidence.price,
            currency=evidence.currency,
            source=evidence.source,
            observed_at=evidence.observed_at,
            availability=evidence.availability,
            source_url=evidence.source_url,
            original_price=evidence.original_price,
            original_price_type=evidence.original_price_type,
            official_product_id=evidence.official_product_id,
            official_sku_id=evidence.official_sku_id,
            match_score=evidence.match_score,
        )


EvidenceResponse = Annotated[
    PolicyEvidenceResponse | DevicePriceEvidenceResponse,
    Field(discriminator="type"),
]


def evidence_from_domain(evidence: Evidence) -> EvidenceResponse:
    if isinstance(evidence, PolicyEvidence):
        return PolicyEvidenceResponse.from_domain(evidence)
    return DevicePriceEvidenceResponse.from_domain(evidence)


class ChatResponse(BaseModel):
    request_id: str
    mode: QueryMode
    answer: str
    evidence: list[EvidenceResponse]
    warnings: list[str]
    missing_fields: list[str]
    used_tool: str
    finish_reason: str
    reason_code: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(
        cls,
        *,
        request_id: str,
        mode: QueryMode,
        result: ToolResult,
        finish_reason: str,
    ) -> "ChatResponse":
        return cls(
            request_id=request_id,
            mode=mode,
            answer=result.answer,
            evidence=[
                evidence_from_domain(item) for item in result.evidence
            ],
            warnings=list(result.warnings),
            missing_fields=list(result.missing_fields),
            used_tool=result.tool,
            finish_reason=finish_reason,
            reason_code=result.reason_code,
            usage=dict(result.usage),
        )


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    phase: int
    checks: dict[str, str]
