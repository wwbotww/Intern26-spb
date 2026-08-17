from __future__ import annotations

import asyncio

import pytest

from spb_assistant_api.domain.exceptions import ToolContractError
from spb_assistant_api.domain.models import (
    DevicePriceEvidence,
    PolicyEvidence,
    QueryMode,
    ToolResult,
    ToolStatus,
)
from spb_assistant_api.services.dispatcher import (
    DEVICE_PRICE_TOOL_NAME,
    POLICY_TOOL_NAME,
    QueryDispatcher,
    ToolRegistry,
)

from .fakes import FakeTool


def _result(tool: str, answer: str) -> ToolResult:
    evidence = (
        (
            PolicyEvidence(
                evidence_id="policy-1",
                title="政策",
                source_url="https://example.test/policy",
                excerpt="依据",
            ),
        )
        if tool == POLICY_TOOL_NAME
        else (
            DevicePriceEvidence(
                evidence_id="price-1",
                title="设备",
                brand="品牌",
                model="型号",
                specification="规格",
                price="1.00",
                currency="CNY",
                source="来源",
                observed_at="2026-08-01T00:00:00Z",
            ),
        )
    )
    return ToolResult(
        tool=tool,
        status=ToolStatus.SUCCESS,
        answer=answer,
        evidence=evidence,
    )


def test_dispatcher_calls_only_the_explicitly_selected_tool() -> None:
    policy = FakeTool(
        name=POLICY_TOOL_NAME,
        result=_result(POLICY_TOOL_NAME, "政策回答"),
    )
    price = FakeTool(
        name=DEVICE_PRICE_TOOL_NAME,
        result=_result(DEVICE_PRICE_TOOL_NAME, "价格回答"),
    )
    dispatcher = QueryDispatcher(
        ToolRegistry(
            {
                QueryMode.POLICY: policy,
                QueryMode.DEVICE_PRICE: price,
            }
        )
    )

    result = asyncio.run(
        dispatcher.dispatch(
            mode=QueryMode.POLICY,
            question="需要准备哪些材料？",
        )
    )

    assert result.answer == "政策回答"
    assert policy.questions == ["需要准备哪些材料？"]
    assert price.questions == []


def test_registry_rejects_swapped_tool_mapping() -> None:
    policy = FakeTool(
        name=POLICY_TOOL_NAME,
        result=_result(POLICY_TOOL_NAME, "政策回答"),
    )
    price = FakeTool(
        name=DEVICE_PRICE_TOOL_NAME,
        result=_result(DEVICE_PRICE_TOOL_NAME, "价格回答"),
    )

    with pytest.raises(ValueError, match="policy_knowledge"):
        ToolRegistry(
            {
                QueryMode.POLICY: price,
                QueryMode.DEVICE_PRICE: policy,
            }
        )


def test_registry_requires_both_query_modes() -> None:
    with pytest.raises(ValueError, match="policy"):
        ToolRegistry({})


def test_dispatcher_rejects_mismatched_result_identity() -> None:
    policy = FakeTool(
        name=POLICY_TOOL_NAME,
        result=_result(DEVICE_PRICE_TOOL_NAME, "错误结果"),
    )
    price = FakeTool(
        name=DEVICE_PRICE_TOOL_NAME,
        result=_result(DEVICE_PRICE_TOOL_NAME, "价格回答"),
    )
    dispatcher = QueryDispatcher(
        ToolRegistry(
            {
                QueryMode.POLICY: policy,
                QueryMode.DEVICE_PRICE: price,
            }
        )
    )

    with pytest.raises(ToolContractError):
        asyncio.run(
            dispatcher.dispatch(
                mode=QueryMode.POLICY,
                question="政策问题",
            )
        )


def test_dispatcher_rejects_success_without_evidence() -> None:
    policy = FakeTool(
        name=POLICY_TOOL_NAME,
        result=ToolResult(
            tool=POLICY_TOOL_NAME,
            status=ToolStatus.SUCCESS,
            answer="没有证据的回答",
        ),
    )
    price = FakeTool(
        name=DEVICE_PRICE_TOOL_NAME,
        result=_result(DEVICE_PRICE_TOOL_NAME, "价格回答"),
    )
    dispatcher = QueryDispatcher(
        ToolRegistry(
            {
                QueryMode.POLICY: policy,
                QueryMode.DEVICE_PRICE: price,
            }
        )
    )

    with pytest.raises(ToolContractError, match="必须包含证据"):
        asyncio.run(
            dispatcher.dispatch(
                mode=QueryMode.POLICY,
                question="政策问题",
            )
        )


def test_dispatcher_rejects_evidence_from_wrong_mode() -> None:
    policy = FakeTool(
        name=POLICY_TOOL_NAME,
        result=_result(POLICY_TOOL_NAME, "政策回答"),
    )
    price = FakeTool(
        name=DEVICE_PRICE_TOOL_NAME,
        result=ToolResult(
            tool=DEVICE_PRICE_TOOL_NAME,
            status=ToolStatus.SUCCESS,
            answer="错误证据",
            evidence=policy.result.evidence,
        ),
    )
    dispatcher = QueryDispatcher(
        ToolRegistry(
            {
                QueryMode.POLICY: policy,
                QueryMode.DEVICE_PRICE: price,
            }
        )
    )

    with pytest.raises(ToolContractError, match="错误类型"):
        asyncio.run(
            dispatcher.dispatch(
                mode=QueryMode.DEVICE_PRICE,
                question="价格问题",
            )
        )


def test_success_result_requires_an_answer() -> None:
    with pytest.raises(ValueError, match="answer"):
        ToolResult(
            tool=POLICY_TOOL_NAME,
            status=ToolStatus.SUCCESS,
            answer="  ",
        )
