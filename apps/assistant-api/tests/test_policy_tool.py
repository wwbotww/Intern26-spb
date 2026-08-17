from __future__ import annotations

import asyncio

import pytest

from spb_assistant_api.domain.exceptions import (
    PolicySourceUnavailableError,
    ToolContractError,
    ToolUnavailableError,
)
from spb_assistant_api.domain.models import ToolStatus
from spb_assistant_api.domain.policy import (
    PolicyCitation,
    PolicyQueryResult,
)
from spb_assistant_api.tools.policy import PolicyKnowledgeTool


class FakePolicySource:
    def __init__(
        self,
        result: PolicyQueryResult,
        *,
        unavailable: bool = False,
    ) -> None:
        self.result = result
        self.unavailable = unavailable
        self.questions: list[str] = []
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def query(self, question: str) -> PolicyQueryResult:
        self.questions.append(question)
        if self.unavailable:
            raise PolicySourceUnavailableError("not ready")
        return self.result

    def readiness(self) -> str:
        return "ready" if self.initialized else "not_ready"

    async def close(self) -> None:
        self.closed = True


def _citation(index: int = 1) -> PolicyCitation:
    return PolicyCitation(
        index=index,
        chunk_id=f"chunk-{index}",
        document_id="document-1",
        title="公开政策文件",
        source_url="https://example.test/policy",
        document_no="示例文号",
        published_at="2026-01-01",
        source_org="示例机构",
        section_path="第二章/第十条",
        score=0.8,
        rerank_score=0.92,
        excerpt="申请人应当提交相关证明材料。",
    )


def _success(
    answer: str = "根据公开政策，应提交相关材料[1]。",
) -> PolicyQueryResult:
    return PolicyQueryResult(
        answer=answer,
        citations=(_citation(),),
        finish_reason="stop",
        usage={"total_tokens": 30},
    )


def test_policy_tool_maps_grounded_answer_and_traceable_evidence() -> None:
    source = FakePolicySource(_success())
    tool = PolicyKnowledgeTool(source=source)

    asyncio.run(tool.initialize())
    result = asyncio.run(tool.execute("理赔需要准备什么材料？"))

    assert result.status is ToolStatus.SUCCESS
    assert result.answer.endswith("[1]。")
    assert result.reason_code == "stop"
    assert result.usage == {"total_tokens": 30}
    assert result.evidence[0].evidence_id == "policy-1"
    assert result.evidence[0].chunk_id == "chunk-1"
    assert result.evidence[0].rerank_score == 0.92


@pytest.mark.parametrize(
    "reason",
    ["no_context", "reranker_rejected", "llm_rejected"],
)
def test_policy_tool_maps_all_grounding_rejections(reason: str) -> None:
    source = FakePolicySource(
        PolicyQueryResult(
            answer="上游固定拒答",
            citations=(),
            finish_reason=reason,
        )
    )

    result = asyncio.run(
        PolicyKnowledgeTool(source=source).execute("无依据的问题")
    )

    assert result.status is ToolStatus.NO_MATCH
    assert result.evidence == ()
    assert result.reason_code == reason
    assert "资料不足" in result.answer


@pytest.mark.parametrize(
    "result",
    [
        PolicyQueryResult(
            answer="没有引用编号的回答",
            citations=(_citation(),),
            finish_reason="stop",
        ),
        PolicyQueryResult(
            answer="引用不存在的来源[2]。",
            citations=(_citation(),),
            finish_reason="stop",
        ),
        PolicyQueryResult(
            answer="有回答[1]。",
            citations=(),
            finish_reason="stop",
        ),
        PolicyQueryResult(
            answer="拒答",
            citations=(_citation(),),
            finish_reason="llm_rejected",
        ),
    ],
)
def test_policy_tool_rejects_invalid_grounding_contract(
    result: PolicyQueryResult,
) -> None:
    source = FakePolicySource(result)

    with pytest.raises(ToolContractError):
        asyncio.run(
            PolicyKnowledgeTool(source=source).execute("政策问题")
        )


def test_policy_tool_forwards_material_and_process_questions_unchanged() -> None:
    source = FakePolicySource(_success())
    tool = PolicyKnowledgeTool(source=source)

    asyncio.run(tool.execute("理赔需要准备什么材料？"))
    asyncio.run(tool.execute("公开办理流程是什么？"))

    assert source.questions == [
        "理赔需要准备什么材料？",
        "公开办理流程是什么？",
    ]


def test_policy_tool_blocks_cross_category_request_before_http_query() -> None:
    source = FakePolicySource(_success())

    result = asyncio.run(
        PolicyKnowledgeTool(source=source).execute(
            "iPhone 16 Pro 多少钱，同时理赔需要哪些材料？"
        )
    )

    assert result.status is ToolStatus.NEED_MORE_INFO
    assert result.reason_code == "multiple_query_categories"
    assert result.missing_fields == ("single_query_category",)
    assert source.questions == []


def test_policy_tool_maps_source_unavailability_to_tool_unavailable() -> None:
    source = FakePolicySource(_success(), unavailable=True)

    with pytest.raises(ToolUnavailableError):
        asyncio.run(
            PolicyKnowledgeTool(source=source).execute("政策问题")
        )
