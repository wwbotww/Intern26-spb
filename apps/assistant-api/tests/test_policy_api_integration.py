from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from spb_assistant_api.adapters.rag_policy import RagPolicyClient
from spb_assistant_api.api.app import create_app
from spb_assistant_api.domain.models import (
    DevicePriceEvidence,
    QueryMode,
    ToolResult,
    ToolStatus,
)
from spb_assistant_api.services.dispatcher import DEVICE_PRICE_TOOL_NAME
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.tools.policy import PolicyKnowledgeTool

from .fakes import FakeTool


def test_policy_http_adapter_flows_through_json_and_sse_api() -> None:
    upstream_questions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/auth/check":
            return httpx.Response(200, json={"status": "ok"})
        payload = json.loads(request.content)
        upstream_questions.append(str(payload["question"]))
        return httpx.Response(
            200,
            json={
                "request_id": "upstream",
                "model": "deepseek",
                "answer": "公开政策要求提交证明材料[1]。",
                "citations": [
                    {
                        "index": 1,
                        "chunk_id": "chunk-1",
                        "document_id": "document-1",
                        "title": "公开政策",
                        "source_url": "https://example.test/policy",
                        "document_no": "示例文号",
                        "published_at": "2026-01-01",
                        "source_org": "示例机构",
                        "section_path": "第二章/第十条",
                        "score": 0.8,
                        "rerank_score": 0.9,
                        "excerpt": "应提交证明材料。",
                    }
                ],
                "usage": {"total_tokens": 20},
                "finish_reason": "stop",
            },
        )

    source = RagPolicyClient(
        base_url="http://rag-api.test",
        api_key="rag-key",
        timeout_seconds=10,
        health_timeout_seconds=2,
        top_k=5,
        candidate_k=40,
        max_connections=5,
        verify_tls=True,
        transport=httpx.MockTransport(handler),
    )
    price = FakeTool(
        name=DEVICE_PRICE_TOOL_NAME,
        result=ToolResult(
            tool=DEVICE_PRICE_TOOL_NAME,
            status=ToolStatus.SUCCESS,
            answer="价格回答",
            evidence=(
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
            ),
        ),
    )
    app = create_app(
        settings=AssistantSettings(
            auth_enabled=False,
            rate_limit_enabled=False,
        ),
        tools={
            QueryMode.POLICY: PolicyKnowledgeTool(source=source),
            QueryMode.DEVICE_PRICE: price,
        },
    )

    with TestClient(app) as client:
        json_response = client.post(
            "/v1/chat",
            headers={"X-Request-ID": "policy-json"},
            json={
                "mode": "policy",
                "question": "理赔需要哪些公开材料？",
                "stream": False,
            },
        )
        stream_response = client.post(
            "/v1/chat",
            json={
                "mode": "policy",
                "question": "公开办理流程是什么？",
                "stream": True,
            },
        )
        split_response = client.post(
            "/v1/chat",
            json={
                "mode": "policy",
                "question": (
                    "iPhone 16 Pro 多少钱，同时理赔要什么材料？"
                ),
                "stream": False,
            },
        )

    assert json_response.status_code == 200
    body = json_response.json()
    assert body["used_tool"] == "policy_knowledge"
    assert body["finish_reason"] == "stop"
    assert body["reason_code"] == "stop"
    assert body["evidence"][0]["type"] == "policy"
    assert body["evidence"][0]["chunk_id"] == "chunk-1"
    assert "event: evidence" in stream_response.text
    assert '"reason_code": "stop"' in stream_response.text
    assert split_response.json()["finish_reason"] == (
        "insufficient_information"
    )
    assert split_response.json()["reason_code"] == (
        "multiple_query_categories"
    )
    assert upstream_questions == [
        "理赔需要哪些公开材料？",
        "公开办理流程是什么？",
    ]
    assert price.questions == []
