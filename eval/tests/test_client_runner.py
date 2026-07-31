from __future__ import annotations

import asyncio
import json

import httpx

from spb_eval.client import RagApiClient
from spb_eval.runner import run_evaluation
from spb_eval.schemas import EvalCase, RunConfig


def test_runner_uses_http_contract_and_does_not_expose_api_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health/live":
            return httpx.Response(
                200,
                json={"status": "ok", "version": "0.5.0", "phase": 5},
            )
        payload = json.loads(request.content)
        if request.url.path == "/v1/retrieve":
            assert payload["query"] == "许可条件是什么？"
            return httpx.Response(
                200,
                json={
                    "query": payload["query"],
                    "mode": "hybrid_rrf_rerank",
                    "count": 1,
                    "elapsed_ms": 10,
                    "results": [
                        {
                            "rank": 1,
                            "document_id": "doc-1",
                            "source_url": "https://example.com/policy",
                            "title": "政策",
                            "score": 0.1,
                            "rerank_score": 0.9,
                        }
                    ],
                },
            )
        assert request.url.path == "/v1/chat"
        assert payload["stream"] is False
        return httpx.Response(
            200,
            json={
                "request_id": "request-1",
                "model": "deepseek",
                "answer": "申请人需要满足许可条件。",
                "citations": [
                    {
                        "document_id": "doc-1",
                        "source_url": "https://example.com/policy",
                        "title": "政策",
                    }
                ],
                "usage": {"total_tokens": 20},
                "finish_reason": "stop",
            },
        )

    async def scenario():
        client = RagApiClient(
            base_url="http://test",
            api_key="secret-test-key",
            timeout_seconds=10,
            transport=httpx.MockTransport(handler),
        )
        try:
            return await run_evaluation(
                client=client,
                cases=[
                    EvalCase(
                        id="case-1",
                        category="direct",
                        question="许可条件是什么？",
                        expected_outcome="answer",
                        gold_document_ids=["doc-1"],
                        required_facts=[["许可条件"]],
                    )
                ],
                config=RunConfig(
                    label="test",
                    base_url="http://test",
                    dataset="cases.jsonl",
                    mode="all",
                    top_k=5,
                    candidate_k=40,
                    concurrency=5,
                    timeout_seconds=10,
                ),
            )
        finally:
            await client.close()

    report = asyncio.run(scenario())

    assert report.summary["retrieval"]["recall_at_5"] == 1.0
    assert report.summary["answers"]["required_fact_coverage"] == 1.0
    assert report.summary["efficiency"]["wall_elapsed_ms"] >= 0
    assert (
        report.summary["efficiency"]["throughput_requests_per_second"]
        is not None
    )
    assert report.service["version"] == "0.5.0"
    assert all(
        request.headers.get("x-api-key") == "secret-test-key"
        for request in requests[1:]
    )
    assert "secret-test-key" not in report.model_dump_json()


def test_client_turns_invalid_upstream_contract_into_case_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    async def scenario():
        client = RagApiClient(
            base_url="http://test",
            api_key="",
            timeout_seconds=10,
            transport=httpx.MockTransport(handler),
        )
        try:
            case = EvalCase(
                id="reject",
                category="out_of_domain",
                question="无关问题",
                expected_outcome="reject",
            )
            return (
                await client.retrieve(case, top_k=5, candidate_k=40),
                await client.chat(case, top_k=5, candidate_k=40),
            )
        finally:
            await client.close()

    retrieval, chat = asyncio.run(scenario())

    assert retrieval.status == "error"
    assert retrieval.error == "retrieve 响应不是 JSON 对象"
    assert chat.status == "error"
    assert chat.error == "chat 响应不是 JSON 对象"
