from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spb_assistant_api.adapters.checkpointer_factory import (
    create_in_memory_checkpointer,
)
from spb_assistant_api.adapters.in_memory_receipts import (
    InMemoryToolExecutionRepository,
)
from spb_assistant_api.adapters.legacy_agent_tools import (
    DevicePriceAssistantToolAdapter,
    PolicyAssistantToolAdapter,
)
from spb_assistant_api.agent_demo import create_demo_app
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.commands import (
    DevicePriceCommand,
    PolicyCommand,
    TrackingCommand,
)
from spb_assistant_api.domain.exceptions import (
    PriceRepositoryError,
    ToolUnavailableError,
)
from spb_assistant_api.domain.failures import FailureCategory
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
)
from spb_assistant_api.services.result_validator import AgentResultValidator
from spb_assistant_api.workflow.composition import create_agent_runtime

from .fakes import FakeTool


NOW = datetime(2026, 9, 4, 8, tzinfo=UTC)


def _policy_result(
    *,
    status: ToolStatus = ToolStatus.SUCCESS,
) -> ToolResult:
    evidence = (
        PolicyEvidence(
            evidence_id="policy-1",
            title="公开政策",
            source_url="https://example.test/policy",
            excerpt="申请人应提交证明材料。",
            document_no="示例文号",
            published_at="2026-01-01",
            source_org="示例机构",
            section_path="第二章/第十条",
            chunk_id="chunk-1",
            document_id="document-1",
            score=0.88,
            rerank_score=0.93,
        ),
    )
    return ToolResult(
        tool=POLICY_TOOL_NAME,
        status=status,
        answer="公开政策要求提交证明材料[1]。",
        evidence=evidence,
        warnings=("请结合原文核验。",),
        reason_code="stop",
    )


def _device_result() -> ToolResult:
    return ToolResult(
        tool=DEVICE_PRICE_TOOL_NAME,
        status=ToolStatus.SUCCESS,
        answer="查询到 1 条设备参考价格记录。",
        evidence=(
            DevicePriceEvidence(
                evidence_id="price-1",
                title="Apple iPhone 16 Pro",
                brand="Apple",
                model="iPhone 16 Pro",
                specification="256GB / 黑色",
                price="7999.00",
                currency="CNY",
                source="官方商城",
                observed_at="2026-09-01T00:00:00Z",
                availability="ON_SALE",
                source_url="https://example.test/device/1",
                original_price="8999.00",
                original_price_type="LIST_PRICE",
                official_product_id="iphone-16-pro",
                official_sku_id="iphone-16-pro-256gb",
                match_score=100,
            ),
        ),
    )


class _RaisingTool:
    def __init__(self, name: str, error: Exception) -> None:
        self._name = name
        self._error = error

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self) -> None:
        return None

    async def execute(self, question: str) -> ToolResult:
        del question
        raise self._error

    def readiness(self) -> str:
        return "ready"

    async def close(self) -> None:
        return None


def test_policy_adapter_projects_full_evidence_and_provenance() -> None:
    tool = FakeTool(name=POLICY_TOOL_NAME, result=_policy_result())
    adapter = PolicyAssistantToolAdapter(tool)

    result = asyncio.run(
        adapter.execute(PolicyCommand(question="理赔需要哪些材料？"))
    )

    assert result.status.value == "success"
    assert result.data is not None
    assert result.data.evidence_ids == ["policy-1"]
    assert result.data.evidence[0].chunk_id == "chunk-1"
    assert result.provenance[0].record_id == "policy-1"
    assert result.warnings == ["请结合原文核验。"]
    assert tool.questions == ["理赔需要哪些材料？"]


def test_device_adapter_preserves_price_evidence_fields() -> None:
    tool = FakeTool(name=DEVICE_PRICE_TOOL_NAME, result=_device_result())
    adapter = DevicePriceAssistantToolAdapter(tool)

    result = asyncio.run(
        adapter.execute(
            DevicePriceCommand(question="iPhone 16 Pro 256GB 多少钱")
        )
    )

    assert result.data is not None
    evidence = result.data.evidence[0]
    assert evidence.price == "7999.00"
    assert evidence.original_price == "8999.00"
    assert evidence.official_sku_id == "iphone-16-pro-256gb"
    assert result.provenance[0].source_name == "官方商城"


@pytest.mark.parametrize(
    ("error", "category", "code"),
    [
        (
            ToolUnavailableError(POLICY_TOOL_NAME),
            FailureCategory.UPSTREAM_UNAVAILABLE,
            "legacy_policy_tool_unavailable",
        ),
        (
            PriceRepositoryError("query failed"),
            FailureCategory.UPSTREAM_UNAVAILABLE,
            "legacy_device_price_upstream_failed",
        ),
    ],
)
def test_adapter_translates_expected_v1_failures(
    error: Exception,
    category: FailureCategory,
    code: str,
) -> None:
    is_policy = isinstance(error, ToolUnavailableError)
    adapter = (
        PolicyAssistantToolAdapter(_RaisingTool(POLICY_TOOL_NAME, error))
        if is_policy
        else DevicePriceAssistantToolAdapter(
            _RaisingTool(DEVICE_PRICE_TOOL_NAME, error)
        )
    )
    command = (
        PolicyCommand(question="政策问题")
        if is_policy
        else DevicePriceCommand(question="设备价格问题")
    )

    with pytest.raises(AgentOperationError) as raised:
        asyncio.run(adapter.execute(command))

    assert raised.value.failure.category is category
    assert raised.value.failure.code == code
    assert raised.value.failure.retryable


def test_adapter_fails_closed_on_wrong_evidence_or_command_type() -> None:
    invalid = ToolResult(
        tool=POLICY_TOOL_NAME,
        status=ToolStatus.SUCCESS,
        answer="错误证据",
        evidence=_device_result().evidence,
    )
    adapter = PolicyAssistantToolAdapter(
        FakeTool(name=POLICY_TOOL_NAME, result=invalid)
    )
    reported_error = PolicyAssistantToolAdapter(
        FakeTool(
            name=POLICY_TOOL_NAME,
            result=ToolResult(
                tool=POLICY_TOOL_NAME,
                status=ToolStatus.ERROR,
                answer="legacy error",
            ),
        )
    )

    with pytest.raises(AgentOperationError) as evidence_error:
        asyncio.run(adapter.execute(PolicyCommand(question="政策问题")))
    with pytest.raises(AgentOperationError) as command_error:
        asyncio.run(adapter.execute(TrackingCommand(mail_no="AB123")))
    with pytest.raises(AgentOperationError) as status_error:
        asyncio.run(
            reported_error.execute(PolicyCommand(question="政策问题"))
        )

    assert evidence_error.value.failure.category is (
        FailureCategory.CONTRACT_VIOLATION
    )
    assert evidence_error.value.failure.code == (
        "legacy_policy_contract_violation"
    )
    assert command_error.value.failure.code == (
        "legacy_policy_command_type_mismatch"
    )
    assert status_error.value.failure.category is (
        FailureCategory.INTERNAL_ERROR
    )
    assert status_error.value.failure.code == (
        "legacy_policy_tool_reported_error"
    )


def test_result_validator_rejects_tampered_legacy_provenance() -> None:
    adapter = PolicyAssistantToolAdapter(
        FakeTool(name=POLICY_TOOL_NAME, result=_policy_result())
    )
    command = PolicyCommand(question="政策问题")
    result = asyncio.run(adapter.execute(command)).model_copy(
        update={"provenance": []}
    )

    with pytest.raises(AgentOperationError) as raised:
        AgentResultValidator().validate(command=command, result=result)

    assert raised.value.failure.code == "policy_provenance_mismatch"


def test_langgraph_routes_both_borrowed_v1_tools_without_duplication() -> None:
    policy = FakeTool(name=POLICY_TOOL_NAME, result=_policy_result())
    device = FakeTool(name=DEVICE_PRICE_TOOL_NAME, result=_device_result())
    runtime = create_agent_runtime(
        checkpointer=create_in_memory_checkpointer(),
        receipts=InMemoryToolExecutionRepository(),
        policy_tool=policy,
        device_price_tool=device,
        clock=lambda: NOW,
    )

    policy_output = asyncio.run(
        runtime.start(
            thread_id="phase4d-policy",
            message="快件丢失理赔需要哪些材料？",
        )
    )
    device_output = asyncio.run(
        runtime.start(
            thread_id="phase4d-device",
            message="iPhone 16 Pro 256GB 多少钱？",
        )
    )

    assert policy_output["phase"] == "completed"
    assert policy_output["result"]["tool"] == POLICY_TOOL_NAME
    assert policy_output["result"]["data"]["evidence"][0]["chunk_id"] == (
        "chunk-1"
    )
    assert device_output["phase"] == "completed"
    assert device_output["result"]["tool"] == DEVICE_PRICE_TOOL_NAME
    assert device_output["result"]["data"]["evidence"][0]["price"] == (
        "7999.00"
    )
    assert len(policy.questions) == 1
    assert len(device.questions) == 1


def test_demo_shares_policy_and_price_tools_across_v1_and_v2(
    tmp_path: Path,
) -> None:
    app = create_demo_app(database_path=tmp_path / "phase4d-demo.db")
    policy_tool = app.state.registry.get(QueryMode.POLICY)
    original_initialize = policy_tool.initialize
    original_close = policy_tool.close
    lifecycle_calls = {"initialize": 0, "close": 0}

    async def counted_initialize() -> None:
        lifecycle_calls["initialize"] += 1
        await original_initialize()

    async def counted_close() -> None:
        lifecycle_calls["close"] += 1
        await original_close()

    policy_tool.initialize = counted_initialize  # type: ignore[method-assign]
    policy_tool.close = counted_close  # type: ignore[method-assign]

    with TestClient(app) as client:
        capabilities = client.get("/v2/agent/capabilities")
        readiness = client.get("/v2/agent/health/ready")
        v1_policy = client.post(
            "/v1/chat",
            json={
                "mode": "policy",
                "question": "快件丢失理赔需要哪些材料？",
                "stream": False,
            },
        )
        v2_policy = client.post(
            "/v2/agent/messages",
            headers={"Idempotency-Key": "phase4d-policy"},
            json={
                "message": "快件丢失理赔需要哪些材料？",
                "explicit_intent": "policy",
            },
        )
        v2_device = client.post(
            "/v2/agent/messages",
            headers={"Idempotency-Key": "phase4d-device"},
            json={
                "message": "iPhone 16 Pro 256GB 多少钱？",
                "explicit_intent": "device_price",
            },
        )

    capability_map = {
        item["intent"]: item for item in capabilities.json()
    }
    assert capability_map["policy"]["capability_version"] == (
        "phase-4d-v1-compat"
    )
    assert capability_map["device_price"]["capability_version"] == (
        "phase-4d-v1-compat"
    )
    assert readiness.status_code == 200
    assert readiness.json()["checks"]["capability.policy"] == "ready"
    assert readiness.json()["checks"]["capability.device_price"] == "ready"
    assert v1_policy.status_code == 200
    assert v1_policy.json()["evidence"][0]["chunk_id"] == (
        "demo-policy-chunk-1"
    )
    assert v2_policy.status_code == 200
    policy_data = v2_policy.json()["result"]["data"]
    assert policy_data["evidence_ids"] == ["policy-1"]
    assert policy_data["evidence"][0]["chunk_id"] == (
        "demo-policy-chunk-1"
    )
    assert v2_device.status_code == 200
    device_data = v2_device.json()["result"]["data"]
    assert device_data["evidence_ids"] == ["price-1"]
    assert device_data["evidence"][0]["price"] == "7999.00"
    assert lifecycle_calls == {"initialize": 1, "close": 1}
