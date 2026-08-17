from __future__ import annotations

from fastapi.testclient import TestClient

from spb_assistant_api.api.app import create_app
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
from spb_assistant_api.settings import AssistantSettings

from .fakes import FakeTool


def _settings(**overrides: object) -> AssistantSettings:
    return AssistantSettings(
        auth_enabled=False,
        rate_limit_enabled=False,
        **overrides,
    )


def _tools() -> tuple[dict[QueryMode, FakeTool], FakeTool, FakeTool]:
    policy = FakeTool(
        name=POLICY_TOOL_NAME,
        result=ToolResult(
            tool=POLICY_TOOL_NAME,
            status=ToolStatus.SUCCESS,
            answer="根据公开政策，应提交能够证明事项的材料[policy-1]。",
            evidence=(
                PolicyEvidence(
                    evidence_id="policy-1",
                    title="示例政策",
                    source_url="https://example.test/policy",
                    excerpt="应当提交相关证明材料。",
                    document_no="示例文号",
                    source_org="示例机构",
                ),
            ),
            usage={"total_tokens": 20},
        ),
    )
    price = FakeTool(
        name=DEVICE_PRICE_TOOL_NAME,
        result=ToolResult(
            tool=DEVICE_PRICE_TOOL_NAME,
            status=ToolStatus.SUCCESS,
            answer="匹配到一个设备参考价格，请以证据卡片为准。",
            evidence=(
                DevicePriceEvidence(
                    evidence_id="price-1",
                    title="示例设备 256GB",
                    brand="示例品牌",
                    model="示例型号",
                    specification="256GB",
                    price="3999.00",
                    currency="CNY",
                    source="测试来源",
                    observed_at="2026-08-01T00:00:00Z",
                    original_price="4299.00",
                    original_price_type="LIST_PRICE",
                    official_product_id="product-1",
                    official_sku_id="sku-1",
                    match_score=98.5,
                ),
            ),
        ),
    )
    return (
        {
            QueryMode.POLICY: policy,
            QueryMode.DEVICE_PRICE: price,
        },
        policy,
        price,
    )


def test_non_stream_request_uses_only_selected_policy_tool() -> None:
    tools, policy, price = _tools()
    app = create_app(settings=_settings(), tools=tools)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            headers={"X-Request-ID": "policy-request"},
            json={
                "mode": "policy",
                "question": "需要准备什么材料？",
                "stream": False,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"] == "policy-request"
    assert payload["mode"] == "policy"
    assert payload["used_tool"] == POLICY_TOOL_NAME
    assert payload["evidence"][0]["type"] == "policy"
    assert payload["finish_reason"] == "stop"
    assert policy.questions == ["需要准备什么材料？"]
    assert price.questions == []


def test_stream_request_emits_status_evidence_delta_and_done() -> None:
    tools, policy, price = _tools()
    app = create_app(settings=_settings(), tools=tools)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            headers={"X-Request-ID": "price-request"},
            json={
                "mode": "device_price",
                "question": "示例型号 256GB 多少钱？",
                "stream": True,
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "text/event-stream"
    )
    assert "event: status" in response.text
    assert "event: evidence" in response.text
    assert "event: delta" in response.text
    assert "event: done" in response.text
    assert '"used_tool": "device_price"' in response.text
    assert '"type": "device_price"' in response.text
    assert '"original_price": "4299.00"' in response.text
    assert '"official_sku_id": "sku-1"' in response.text
    assert policy.questions == []
    assert price.questions == ["示例型号 256GB 多少钱？"]


def test_request_rejects_missing_invalid_mode_and_history() -> None:
    tools, _, _ = _tools()
    app = create_app(settings=_settings(), tools=tools)

    with TestClient(app) as client:
        missing = client.post(
            "/v1/chat",
            json={"question": "问题", "stream": False},
        )
        invalid = client.post(
            "/v1/chat",
            json={
                "mode": "auto",
                "question": "问题",
                "stream": False,
            },
        )
        history = client.post(
            "/v1/chat",
            json={
                "mode": "policy",
                "question": "问题",
                "stream": False,
                "history": [{"role": "user", "content": "上一轮"}],
            },
        )

    assert missing.status_code == 422
    assert invalid.status_code == 422
    assert history.status_code == 422
    assert "extra_forbidden" in str(history.json())


def test_sequential_requests_do_not_share_question_context() -> None:
    tools, _, price = _tools()
    app = create_app(settings=_settings(), tools=tools)

    with TestClient(app) as client:
        first = client.post(
            "/v1/chat",
            json={
                "mode": "device_price",
                "question": "示例型号 256GB 多少钱？",
                "stream": False,
            },
        )
        second = client.post(
            "/v1/chat",
            json={
                "mode": "device_price",
                "question": "这个型号呢？",
                "stream": False,
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert price.questions == [
        "示例型号 256GB 多少钱？",
        "这个型号呢？",
    ]


def test_default_unavailable_tool_is_not_reported_as_an_answer() -> None:
    app = create_app(settings=_settings())

    with TestClient(app) as client:
        json_response = client.post(
            "/v1/chat",
            json={
                "mode": "policy",
                "question": "政策问题",
                "stream": False,
            },
        )
        stream_response = client.post(
            "/v1/chat",
            json={
                "mode": "device_price",
                "question": "价格问题",
                "stream": True,
            },
        )

    assert json_response.status_code == 503
    assert json_response.json()["detail"]["code"] == "tool_unavailable"
    assert stream_response.status_code == 200
    assert "event: error" in stream_response.text
    assert '"code": "tool_unavailable"' in stream_response.text
