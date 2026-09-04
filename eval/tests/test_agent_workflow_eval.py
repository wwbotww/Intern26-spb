from __future__ import annotations

import asyncio
import ast
import json
from pathlib import Path

import httpx

from spb_eval.cli import build_parser, main
from spb_eval.client import AgentApiClient
from spb_eval.dataset import load_agent_dataset
from spb_eval.metrics import calculate_agent_metrics
from spb_eval.reporting import write_agent_report
from spb_eval.runner import run_agent_evaluation
from spb_eval.schemas import (
    AgentCaseResult,
    AgentEvalCase,
    AgentEvalThresholds,
    AgentEvalTurn,
    AgentRunConfig,
    AgentRunReport,
    AgentTurnObservation,
    AgentTurnResult,
)


def _response(
    *,
    conversation_id: str,
    turn_id: str,
    phase: str,
    intent: str | None,
    next_action: str,
    required_inputs: list[dict] | None = None,
    result: dict | None = None,
) -> dict:
    return {
        "request_id": f"request-{turn_id}",
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "phase": phase,
        "intent": intent,
        "reply": "测试回复",
        "next_action": next_action,
        "required_inputs": required_inputs or [],
        "result": result,
        "failure": None,
        "warnings": [],
    }


def _tracking_cases() -> list[AgentEvalCase]:
    return [
        AgentEvalCase(
            id="tracking-fill",
            category="multi_turn_slot_fill",
            turns=[
                AgentEvalTurn(
                    message="帮我查邮件",
                    expected_phase="waiting_user",
                    expected_intent="tracking",
                    expected_next_action="collect_slots",
                    expected_required_inputs=["mail_no"],
                ),
                AgentEvalTurn(
                    message="1234567890123",
                    expected_phase="completed",
                    expected_intent="tracking",
                    expected_next_action="complete",
                    expected_result_status="success",
                    expected_result_values={
                        "mail_no": "1234567890123"
                    },
                ),
            ],
        ),
        AgentEvalCase(
            id="policy-answer",
            category="single_turn_success",
            turns=[
                AgentEvalTurn(
                    message="理赔材料政策是什么？",
                    expected_phase="completed",
                    expected_intent="policy",
                    expected_next_action="complete",
                    expected_result_status="success",
                    expected_result_values={"type": "policy"},
                )
            ],
        ),
    ]


def test_public_agent_workflow_dataset_covers_five_intents_and_recovery() -> None:
    workspace_root = Path(__file__).resolve().parents[2]
    cases = load_agent_dataset(
        workspace_root / "eval" / "datasets" / "agent-workflow-v1.jsonl"
    )

    intents = {
        turn.expected_intent
        for case in cases
        for turn in case.turns
        if turn.expected_intent is not None
    }
    categories = {case.category for case in cases}
    split_counts = {
        split: sum(case.split == split for case in cases)
        for split in ("calibration", "holdout")
    }

    assert len(cases) == 13
    assert {
        "policy",
        "device_price",
        "tracking",
        "delivery_time",
        "postage",
    } <= intents
    assert {
        "multi_turn_slot_fill",
        "multi_intent_clarification",
        "business_no_match",
        "tool_need_more_info",
        "unknown_handoff",
    } <= categories
    assert sum(len(case.turns) > 1 for case in cases) == 4
    assert split_counts == {"calibration": 7, "holdout": 6}


def test_agent_runner_uses_v2_http_and_preserves_conversation_context() -> None:
    requests: list[httpx.Request] = []
    active_cases = 0
    max_active_cases = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_cases, max_active_cases
        requests.append(request)
        if request.url.path == "/v2/agent/health/ready":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "service": "spb-assistant-agent-v2",
                    "version": "0.3.2",
                    "phase": 4,
                    "checks": {},
                },
            )
        if request.url.path == "/v2/agent/capabilities":
            return httpx.Response(
                200,
                json=[
                    {
                        "intent": intent,
                        "display_name": intent,
                        "available": True,
                        "capability_version": "fixture-v1",
                        "required_inputs": [],
                    }
                    for intent in (
                        "policy",
                        "device_price",
                        "tracking",
                        "delivery_time",
                        "postage",
                    )
                ],
            )
        assert request.url.path == "/v2/agent/messages"
        assert request.headers["authorization"] == "Bearer secret-agent-key"
        assert request.headers["idempotency-key"].startswith("agent-eval-")
        payload = json.loads(request.content)
        assert payload["stream"] is False
        active_cases += 1
        max_active_cases = max(max_active_cases, active_cases)
        await asyncio.sleep(0.01)
        active_cases -= 1
        if payload.get("message") == "帮我查邮件":
            return httpx.Response(
                200,
                json=_response(
                    conversation_id="00000000-0000-4000-8000-000000000001",
                    turn_id="10000000-0000-4000-8000-000000000001",
                    phase="waiting_user",
                    intent="tracking",
                    next_action="collect_slots",
                    required_inputs=[
                        {
                            "name": "mail_no",
                            "label": "邮件号",
                            "type": "string",
                            "validation_hint": "13 位数字",
                            "choices": [],
                        }
                    ],
                ),
            )
        if payload.get("message") == "1234567890123":
            assert payload["conversation_id"] == (
                "00000000-0000-4000-8000-000000000001"
            )
            return httpx.Response(
                200,
                json=_response(
                    conversation_id=payload["conversation_id"],
                    turn_id="10000000-0000-4000-8000-000000000002",
                    phase="completed",
                    intent="tracking",
                    next_action="complete",
                    result={
                        "type": "tracking",
                        "status": "success",
                        "data": {
                            "type": "tracking",
                            "mail_no": "1234567890123",
                        },
                        "reason_code": "",
                    },
                ),
            )
        return httpx.Response(
            200,
            json=_response(
                conversation_id="00000000-0000-4000-8000-000000000002",
                turn_id="20000000-0000-4000-8000-000000000001",
                phase="completed",
                intent="policy",
                next_action="complete",
                result={
                    "type": "policy",
                    "status": "success",
                    "data": {"type": "policy", "evidence": []},
                    "reason_code": "",
                },
            ),
        )

    async def scenario():
        async with AgentApiClient(
            base_url="http://test",
            api_key="secret-agent-key",
            timeout_seconds=10,
            transport=httpx.MockTransport(handler),
        ) as client:
            return await run_agent_evaluation(
                client=client,
                cases=_tracking_cases(),
                config=AgentRunConfig(
                    label="agent-test",
                    base_url="http://test",
                    dataset="agent.jsonl",
                    concurrency=2,
                    timeout_seconds=10,
                ),
            )

    report = asyncio.run(scenario())

    assert max_active_cases == 2
    assert report.summary["quality_gate"]["passed"] is True
    assert report.summary["understanding"]["intent_accuracy"] == 1.0
    assert report.summary["routing"]["wrong_tool_rate"] == 0.0
    assert report.summary["completion"]["task_completion_rate"] == 1.0
    assert report.summary["recovery"]["recovery_rate"] == 1.0
    assert report.summary["by_split"] == {
        "calibration": {
            "cases": 2,
            "turns": 3,
            "passed": 2,
            "pass_rate": 1.0,
        }
    }
    assert report.service["version"] == "0.3.2"
    assert "secret-agent-key" not in report.model_dump_json()
    assert len(requests) == 5


def test_agent_metrics_and_report_expose_gate_failure_and_review_queue(
    tmp_path: Path,
) -> None:
    case = AgentEvalCase(
        id="timeout-recovery",
        category="failure_recovery",
        turns=[
            AgentEvalTurn(
                message="查邮件",
                expected_phase="waiting_user",
                expected_intent="tracking",
                expected_next_action="collect_slots",
                expected_required_inputs=["mail_no"],
            ),
            AgentEvalTurn(
                message="1234567890123",
                expected_phase="completed",
                expected_intent="tracking",
                expected_next_action="complete",
                expected_result_status="success",
            ),
        ],
    )
    result = AgentCaseResult(
        case=case,
        turns=[
            AgentTurnResult(
                turn_index=1,
                expected=case.turns[0],
                observation=AgentTurnObservation(
                    status="error",
                    client_elapsed_ms=50,
                    http_status=503,
                    error_code="legacy_tracking_unavailable",
                    error_category="upstream_unavailable",
                    retryable=True,
                    error="查询依赖暂时不可用",
                ),
            )
        ],
    )
    summary = calculate_agent_metrics(
        [result],
        thresholds=AgentEvalThresholds(),
    )
    run_report = AgentRunReport(
        generated_at="2026-09-04T12:00:00+00:00",
        config=AgentRunConfig(
            label="gate-failure",
            base_url="http://test",
            dataset="agent.jsonl",
            concurrency=1,
            timeout_seconds=10,
        ),
        summary=summary,
        results=[result],
    )
    report = write_agent_report(run_report, tmp_path)

    assert summary["quality_gate"]["passed"] is False
    assert summary["turns"]["api_error_rate"] == 1.0
    assert summary["cases"]["incomplete"] == 1
    assert (report / "quality-gate.json").is_file()
    assert "FAIL" in (report / "summary.md").read_text(encoding="utf-8")
    review = (report / "review-queue.md").read_text(encoding="utf-8")
    assert "legacy_tracking_unavailable" in review
    assert "基础设施问题" in review


def test_agent_cli_exposes_quality_gate_configuration(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    args = build_parser().parse_args(
        [
            "agent-run",
            "--dataset",
            "agent.jsonl",
            "--min-intent-accuracy",
            "0.9",
            "--max-wrong-tool-rate",
            "0.1",
            "--fail-on-gate",
        ]
    )

    assert args.command == "agent-run"
    assert args.min_intent_accuracy == 0.9
    assert args.max_wrong_tool_rate == 0.1
    assert args.fail_on_gate is True

    async def failed_gate(_args):
        return tmp_path / "report", False

    monkeypatch.setattr("spb_eval.cli._run_agent", failed_gate)
    exit_code = main(
        [
            "agent-run",
            "--dataset",
            "agent.jsonl",
            "--fail-on-gate",
        ]
    )

    assert exit_code == 3
    output = json.loads(capsys.readouterr().out)
    assert output["quality_gate_passed"] is False


def test_eval_runtime_does_not_import_assistant_implementation() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "spb_eval"
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            violations.extend(
                f"{path.name}: {module}"
                for module in modules
                if module.startswith("spb_assistant_api")
            )

    assert violations == []
