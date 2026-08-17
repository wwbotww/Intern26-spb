from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from spb_assistant_api.adapters.rag_policy import RagPolicyClient
from spb_assistant_api.domain.exceptions import (
    PolicySourceContractError,
    PolicySourceUnavailableError,
)
from spb_assistant_api.observability.context import (
    bind_request_id,
    reset_request_id,
)


def _chat_payload() -> dict[str, object]:
    return {
        "request_id": "upstream-request",
        "model": "deepseek",
        "answer": "根据规定，应提交相关材料[1]。",
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
                "excerpt": "应提交相关材料。",
            }
        ],
        "usage": {"total_tokens": 20},
        "finish_reason": "stop",
    }


def _client(transport: httpx.MockTransport) -> RagPolicyClient:
    return RagPolicyClient(
        base_url="http://rag-api.test",
        api_key="internal-rag-key",
        timeout_seconds=10,
        health_timeout_seconds=2,
        top_k=5,
        candidate_k=40,
        max_connections=5,
        verify_tls=True,
        transport=transport,
    )


def test_rag_policy_client_uses_json_contract_auth_and_request_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/auth/check":
            captured["auth_probe_authorization"] = request.headers.get(
                "authorization"
            )
            return httpx.Response(
                200,
                json={"status": "ok", "service": "spb-rag-api"},
            )
        captured["authorization"] = request.headers.get("authorization")
        captured["request_id"] = request.headers.get("x-request-id")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_payload())

    client = _client(httpx.MockTransport(handler))

    async def scenario() -> None:
        token = bind_request_id("assistant-request")
        try:
            await client.initialize()
            result = await client.query("理赔材料有哪些？")
            assert result.finish_reason == "stop"
            assert result.citations[0].chunk_id == "chunk-1"
            assert result.usage == {"total_tokens": 20}
        finally:
            reset_request_id(token)
            await client.close()

    asyncio.run(scenario())

    assert captured["authorization"] == "Bearer internal-rag-key"
    assert captured["auth_probe_authorization"] == (
        "Bearer internal-rag-key"
    )
    assert captured["request_id"] == "assistant-request"
    assert captured["payload"] == {
        "question": "理赔材料有哪些？",
        "stream": False,
        "top_k": 5,
        "candidate_k": 40,
    }


def test_rag_policy_client_rejects_invalid_success_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/auth/check":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"answer": "missing fields"})

    client = _client(httpx.MockTransport(handler))

    async def scenario() -> None:
        try:
            await client.initialize()
            with pytest.raises(PolicySourceContractError):
                await client.query("政策问题")
        finally:
            await client.close()

    asyncio.run(scenario())


def test_rag_policy_client_marks_upstream_503_as_not_ready() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/auth/check":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(503, json={"detail": {"code": "down"}})

    client = _client(httpx.MockTransport(handler))

    async def scenario() -> None:
        try:
            await client.initialize()
            with pytest.raises(PolicySourceUnavailableError):
                await client.query("政策问题")
            assert client.readiness() == "not_ready"
        finally:
            await client.close()

    asyncio.run(scenario())
    assert requests == 3


def test_rag_policy_client_does_not_query_when_health_is_not_ready() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(503, json={"status": "not_ready"})

    client = _client(httpx.MockTransport(handler))

    async def scenario() -> None:
        try:
            with pytest.raises(PolicySourceUnavailableError):
                await client.query("政策问题")
        finally:
            await client.close()

    asyncio.run(scenario())
    assert paths == ["/health/ready"]


def test_rag_policy_client_does_not_report_ready_with_invalid_key() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(
            401,
            json={"detail": {"code": "unauthorized"}},
        )

    client = _client(httpx.MockTransport(handler))

    async def scenario() -> None:
        try:
            await client.initialize()
            assert client.readiness() == "not_ready"
            with pytest.raises(PolicySourceUnavailableError):
                await client.query("政策问题")
        finally:
            await client.close()

    asyncio.run(scenario())
    assert paths == [
        "/health/ready",
        "/v1/auth/check",
        "/health/ready",
        "/v1/auth/check",
    ]
