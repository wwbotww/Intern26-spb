from __future__ import annotations

import re

from ..domain.exceptions import (
    PolicySourceContractError,
    PolicySourceUnavailableError,
    ToolContractError,
    ToolUnavailableError,
)
from ..domain.models import PolicyEvidence, ToolResult, ToolStatus
from ..domain.policy import PolicyCitation, PolicyQueryResult
from ..domain.ports import PolicyKnowledgeSource
from .query_scope import cross_category_result, is_cross_category_question


POLICY_TOOL_NAME = "policy_knowledge"
REJECTION_REASONS = frozenset(
    {"no_context", "reranker_rejected", "llm_rejected"}
)
REFERENCE_RE = re.compile(r"\[(\d+)]")
NO_POLICY_ANSWER = (
    "当前公开政策知识库资料不足以回答该问题。"
    "请补充更具体的事项、材料或办理场景，"
    "或通过正式渠道核实。"
)


class PolicyKnowledgeTool:
    def __init__(self, *, source: PolicyKnowledgeSource) -> None:
        self._source = source

    @property
    def name(self) -> str:
        return POLICY_TOOL_NAME

    async def initialize(self) -> None:
        await self._source.initialize()

    async def execute(self, question: str) -> ToolResult:
        if is_cross_category_question(question):
            return cross_category_result(self.name)
        try:
            result = await self._source.query(question)
        except PolicySourceContractError as exc:
            raise ToolContractError(
                "政策服务返回了无效响应"
            ) from exc
        except PolicySourceUnavailableError as exc:
            raise ToolUnavailableError(self.name) from exc
        if result.finish_reason in REJECTION_REASONS:
            if result.citations:
                raise ToolContractError(
                    "政策服务拒答时不应返回引用证据"
                )
            return ToolResult(
                tool=self.name,
                status=ToolStatus.NO_MATCH,
                answer=NO_POLICY_ANSWER,
                reason_code=result.finish_reason,
            )
        self._validate_success(result)
        status = (
            ToolStatus.SUCCESS
            if result.finish_reason == "stop"
            else ToolStatus.PARTIAL
        )
        warnings = (
            ()
            if status is ToolStatus.SUCCESS
            else ("政策回答未正常完整结束，请结合引用原文核验。",)
        )
        return ToolResult(
            tool=self.name,
            status=status,
            answer=result.answer.strip(),
            evidence=tuple(
                self._to_evidence(citation)
                for citation in result.citations
            ),
            warnings=warnings,
            usage=dict(result.usage),
            reason_code=result.finish_reason,
        )

    @staticmethod
    def _validate_success(result: PolicyQueryResult) -> None:
        answer = result.answer.strip()
        if not answer or not result.citations:
            raise ToolContractError(
                "政策服务成功响应必须包含回答和引用"
            )
        indices = [citation.index for citation in result.citations]
        expected = list(range(1, len(indices) + 1))
        if indices != expected or len(set(indices)) != len(indices):
            raise ToolContractError("政策引用编号不连续或重复")
        if len({item.chunk_id for item in result.citations}) != len(indices):
            raise ToolContractError("政策引用包含重复 chunk")
        if any(
            not citation.chunk_id.strip()
            or not citation.title.strip()
            or not citation.source_url.startswith(("http://", "https://"))
            or not citation.excerpt.strip()
            for citation in result.citations
        ):
            raise ToolContractError("政策引用缺少可追溯字段")
        references = {
            int(value) for value in REFERENCE_RE.findall(answer)
        }
        if not references:
            raise ToolContractError("政策回答没有引用编号")
        if not references.issubset(set(indices)):
            raise ToolContractError("政策回答引用了不存在的证据")

    @staticmethod
    def _to_evidence(citation: PolicyCitation) -> PolicyEvidence:
        return PolicyEvidence(
            evidence_id=f"policy-{citation.index}",
            title=citation.title,
            source_url=citation.source_url,
            excerpt=citation.excerpt,
            document_no=citation.document_no,
            published_at=citation.published_at,
            source_org=citation.source_org,
            section_path=citation.section_path,
            chunk_id=citation.chunk_id,
            document_id=citation.document_id,
            score=citation.score,
            rerank_score=citation.rerank_score,
        )

    def readiness(self) -> str:
        return self._source.readiness()

    async def close(self) -> None:
        await self._source.close()
