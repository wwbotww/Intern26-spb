from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from spb_eval.cli import build_parser
from spb_eval.client import AssistantApiClient
from spb_eval.dataset import DatasetError, load_assistant_dataset
from spb_eval.metrics import calculate_assistant_metrics
from spb_eval.reporting import write_assistant_report
from spb_eval.runner import run_assistant_evaluation
from spb_eval.schemas import (
    AssistantCaseResult,
    AssistantChatObservation,
    AssistantEvalCase,
    AssistantEvidenceItem,
    AssistantRunConfig,
)


def _policy_evidence() -> dict:
    return {
        "evidence_id": "policy-1",
        "type": "policy",
        "title": "示例政策",
        "source_url": "https://example.test/policy",
        "excerpt": "应当提交相关证明材料。",
        "chunk_id": "chunk-1",
        "document_id": "document-1",
    }


def _price_evidence() -> dict:
    return {
        "evidence_id": "price-1",
        "type": "device_price",
        "title": "示例设备 256GB",
        "price": "3999.00",
        "currency": "CNY",
        "source": "示例商城",
        "observed_at": "2026-08-01T00:00:00Z",
        "official_product_id": "product-1",
        "official_sku_id": "sku-1",
        "match_score": 98.5,
    }


def _response(
    *,
    mode: str,
    finish_reason: str,
    answer: str,
    evidence: list[dict] | None = None,
    reason_code: str = "",
    missing_fields: list[str] | None = None,
) -> dict:
    return {
        "request_id": f"request-{mode}",
        "mode": mode,
        "answer": answer,
        "evidence": evidence or [],
        "warnings": [],
        "missing_fields": missing_fields or [],
        "used_tool": (
            "policy_knowledge" if mode == "policy" else "device_price"
        ),
        "finish_reason": finish_reason,
        "reason_code": reason_code,
        "usage": {"total_tokens": 10 if mode == "policy" else 0},
    }


def _cases() -> list[AssistantEvalCase]:
    return [
        AssistantEvalCase(
            id="policy-answer",
            category="policy",
            mode="policy",
            question="政策回答问题",
            expected_outcome="answer",
        ),
        AssistantEvalCase(
            id="policy-reject",
            category="policy_no_match",
            mode="policy",
            question="政策拒答问题",
            expected_outcome="no_match",
            expected_reason_codes=["no_context"],
        ),
        AssistantEvalCase(
            id="price-answer",
            category="price",
            mode="device_price",
            question="设备价格问题",
            expected_outcome="answer",
            expected_product_ids=["product-1"],
            expected_sku_ids=["sku-1"],
        ),
        AssistantEvalCase(
            id="price-missing",
            category="price_missing",
            mode="device_price",
            question="设备信息不足问题",
            expected_outcome="need_more_info",
            expected_missing_fields=["brand_or_model"],
        ),
        AssistantEvalCase(
            id="cross-category",
            category="cross_category",
            mode="policy",
            question="跨类别问题",
            expected_outcome="need_more_info",
            expected_reason_codes=["multiple_query_categories"],
            expected_missing_fields=["single_query_category"],
        ),
    ]


def test_assistant_runner_checks_contract_concurrency_and_secrets(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    active = 0
    max_active = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        requests.append(request)
        if request.url.path == "/health/live":
            return httpx.Response(
                200,
                json={"status": "ok", "version": "0.3.0", "phase": 3},
            )
        assert request.url.path == "/v1/chat"
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        payload = json.loads(request.content)
        question = payload["question"]
        if question == "政策回答问题":
            response = _response(
                mode="policy",
                finish_reason="stop",
                answer="有依据的政策回答[1]。",
                evidence=[_policy_evidence()],
            )
        elif question == "政策拒答问题":
            response = _response(
                mode="policy",
                finish_reason="no_match",
                answer="未找到政策依据。",
                reason_code="no_context",
            )
        elif question == "设备价格问题":
            response = _response(
                mode="device_price",
                finish_reason="stop",
                answer="找到一条价格候选。",
                evidence=[_price_evidence()],
            )
        elif question == "设备信息不足问题":
            response = _response(
                mode="device_price",
                finish_reason="insufficient_information",
                answer="请补充品牌或型号。",
                missing_fields=["brand_or_model"],
            )
        else:
            response = _response(
                mode="policy",
                finish_reason="insufficient_information",
                answer="请拆分查询。",
                reason_code="multiple_query_categories",
                missing_fields=["single_query_category"],
            )
        return httpx.Response(200, json=response)

    async def scenario():
        client = AssistantApiClient(
            base_url="http://test",
            api_key="secret-assistant-key",
            timeout_seconds=10,
            transport=httpx.MockTransport(handler),
        )
        try:
            return await run_assistant_evaluation(
                client=client,
                cases=_cases(),
                config=AssistantRunConfig(
                    label="assistant-test",
                    base_url="http://test",
                    dataset="assistant.jsonl",
                    concurrency=2,
                    timeout_seconds=10,
                ),
            )
        finally:
            await client.close()

    report = asyncio.run(scenario())
    summary = report.summary

    assert max_active == 2
    assert summary["cases"]["pass_rate"] == 1.0
    assert summary["routing"]["accuracy"] == 1.0
    assert summary["evidence"]["unsupported_evidence_leaks"] == 0
    assert summary["evidence"]["price_candidate_recall"] == 1.0
    assert summary["efficiency"]["wall_elapsed_ms"] > 0
    assert summary["efficiency"]["throughput_requests_per_second"] > 0
    assert all(
        request.headers.get("authorization")
        == "Bearer secret-assistant-key"
        for request in requests[1:]
    )
    for request in requests[1:]:
        assert set(json.loads(request.content)) == {
            "mode",
            "question",
            "stream",
        }
    assert "secret-assistant-key" not in report.model_dump_json()

    output = write_assistant_report(report, tmp_path)
    assert (output / "run.json").is_file()
    assert (output / "cases.jsonl").is_file()
    assert "固定路由准确率" in (output / "summary.md").read_text()
    assert "无待复核样本" in (
        output / "review-queue.md"
    ).read_text()


def test_assistant_client_maps_invalid_contract_to_sanitized_error() -> None:
    async def scenario() -> AssistantChatObservation:
        client = AssistantApiClient(
            base_url="http://test",
            api_key="",
            timeout_seconds=10,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=["invalid"])
            ),
        )
        try:
            return await client.chat(_cases()[0])
        finally:
            await client.close()

    observation = asyncio.run(scenario())

    assert observation.status == "error"
    assert observation.error == "assistant chat 响应不是 JSON 对象"


def test_assistant_metrics_detect_unsupported_price_evidence() -> None:
    result = AssistantCaseResult(
        case=AssistantEvalCase(
            id="leak",
            category="no_match",
            mode="device_price",
            question="不存在的设备",
            expected_outcome="no_match",
        ),
        chat=AssistantChatObservation(
            status="ok",
            client_elapsed_ms=5,
            mode="device_price",
            answer="没有匹配。",
            evidence=[AssistantEvidenceItem.model_validate(_price_evidence())],
            used_tool="device_price",
            finish_reason="no_match",
        ),
    )

    summary = calculate_assistant_metrics([result])

    assert summary["cases"]["pass_rate"] == 0.0
    assert summary["evidence"]["unsupported_evidence_leaks"] == 1


def test_assistant_dataset_and_cli_contract(tmp_path: Path) -> None:
    dataset = tmp_path / "assistant.jsonl"
    case = {
        "id": "missing",
        "category": "price_missing",
        "mode": "device_price",
        "question": "这个多少钱？",
        "expected_outcome": "need_more_info",
        "expected_missing_fields": ["brand_or_model"],
    }
    dataset.write_text(
        json.dumps(case, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    loaded = load_assistant_dataset(dataset)
    args = build_parser().parse_args(
        [
            "assistant-run",
            "--dataset",
            str(dataset),
            "--concurrency",
            "5",
        ]
    )

    assert loaded[0].min_evidence_count == 0
    assert loaded[0].expected_finish_reasons == [
        "insufficient_information"
    ]
    assert args.command == "assistant-run"
    assert args.concurrency == 5

    dataset.write_text(
        json.dumps(case, ensure_ascii=False)
        + "\n"
        + json.dumps(case, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match="样本 ID 重复"):
        load_assistant_dataset(dataset)
