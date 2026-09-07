"""Real V2/Graph/SQLite chain with explicitly scripted provider wire responses.

This is an integration regression, NOT a model-quality or paid-provider experiment.
The provider fixtures below are independent of the Eval Gold and report code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from collections import Counter
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from spb_assistant_api.agent_demo import create_demo_app
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.tools.device_price import DevicePriceTool
from spb_eval.client import AgentApiClient
from spb_eval.dataset import dataset_sha256, load_agent_dataset
from spb_eval.metrics import agent_case_checks
from spb_eval.reporting import write_agent_report
from spb_eval.runner import run_agent_evaluation
from spb_eval.schemas import AgentRunConfig
from spb_eval.understanding_dataset import load_understanding_cases

WORKSPACE = Path(__file__).resolve().parents[3]
DATASET = (
    WORKSPACE
    / "eval/datasets/agent-understanding-workflow-development-v1.jsonl"
)
COMPONENT_DATASET = (
    WORKSPACE / "eval/datasets/query-understanding-development-v1.jsonl"
)

# No code here reads expected_intent/Gold to synthesize model output.
WIRE_FIXTURES = {
    "寄出去的包裹遗失了，作为寄件人该怎样主张我的权益？": '{"selected_intent":"policy","candidates":[{"intent":"policy","score":0.9}]}',
    "想入手苹果十六代 Pro，预算大概得准备多少？": '{"selected_intent":"device_price","candidates":[{"intent":"device_price","score":0.9}]}',
    "我寄出去的那件东西，眼下在什么地方？": '{"selected_intent":"tracking","candidates":[{"intent":"tracking","score":0.9}],"slots":{"intent":"tracking","mail_no":"9999999999999"}}',
    "我今天交给寄递员的东西，收件人大概何时能收到？": '{"selected_intent":"delivery_time","candidates":[{"intent":"delivery_time","score":0.9}]}',
    "从北京寄往上海，收货人哪天可以拿到？": '{"selected_intent":"delivery_time","candidates":[{"intent":"delivery_time","score":0.9}]}',
    "替我把这个包裹送过去，要花多少银子？": '{"selected_intent":"postage","candidates":[{"intent":"postage","score":0.9}]}',
    "这个怎么样？": '{"selected_intent":"unknown","candidates":[]}',
}


class ScriptedProvider(httpx.MockTransport):
    def __init__(self, failure: str | None = None):
        self.calls: list[str] = []
        self.unexpected: list[str] = []
        self.closed = False
        self.failure = failure
        super().__init__(self.respond)

    def respond(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "model.example.test"
        assert request.url.path == "/chat/completions"
        payload = json.loads(request.content)
        message = payload["messages"][1]["content"]
        self.calls.append(message)
        if message not in WIRE_FIXTURES:
            self.unexpected.append(message)
            return httpx.Response(
                500, json={"error": "unscripted_fixture_input"}
            )
        if self.failure == "rate_limit":
            return httpx.Response(429, headers={"Retry-After": "0"})
        content = (
            "not-json"
            if self.failure == "invalid_schema"
            else WIRE_FIXTURES[message]
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    async def aclose(self):
        self.closed = True
        await super().aclose()


def _settings() -> AssistantSettings:
    return AssistantSettings(
        _env_file=None,
        query_model_enabled=True,
        query_model_api_key="phase5d-synthetic-key",
        query_model_base_url="https://model.example.test",
        query_model_timeout_seconds=1,
    )


class ReplayASGITransport(httpx.AsyncBaseTransport):
    """Replay every logical V2 message without letting retries affect scoring."""

    def __init__(self, app, provider: ScriptedProvider):
        self.inner = httpx.ASGITransport(app=app)
        self.provider = provider
        self.replays = 0

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        await response.aread()
        if (
            request.method == "POST"
            and request.url.path == "/v2/agent/messages"
        ):
            assert response.status_code == 200
            calls_before = len(self.provider.calls)
            replay = await self.inner.handle_async_request(
                httpx.Request(
                    request.method,
                    request.url,
                    headers=request.headers,
                    content=await request.aread(),
                )
            )
            await replay.aread()
            assert replay.status_code == 200
            first, second = response.json(), replay.json()
            assert first.pop("request_id") != second.pop("request_id")
            assert first == second
            assert len(self.provider.calls) == calls_before
            await replay.aclose()
            self.replays += 1
        return response

    async def aclose(self):
        await self.inner.aclose()


async def _run(tmp_path: Path, provider: ScriptedProvider, cases=None):
    app = create_demo_app(
        database_path=tmp_path / "workflow.db",
        settings=_settings(),
        query_model_transport=provider,
    )
    wire = ReplayASGITransport(app, provider)
    async with (
        app.router.lifespan_context(app),
        AgentApiClient(
            base_url="http://test",
            api_key="",
            timeout_seconds=5,
            transport=wire,
        ) as client,
    ):
        report = await run_agent_evaluation(
            client=client,
            cases=load_agent_dataset(DATASET) if cases is None else cases,
            config=AgentRunConfig(
                label="phase5d-scripted-provider-development",
                base_url="http://test",
                dataset=DATASET.name,
                dataset_sha256=dataset_sha256(DATASET),
                concurrency=1,
                timeout_seconds=5,
            ),
        )
    report.service["evaluation_fixture"] = {
        "kind": "scripted_model_v2_regression",
        "api_transport": "httpx.ASGITransport",
        "model_transport": "httpx.MockTransport",
        "model_attempts": len(provider.calls),
        "replayed_messages": wire.replays,
        "paid_model_requests": 0,
        "component_dataset_sha256": dataset_sha256(COMPONENT_DATASET),
    }
    return report, wire


def test_component_linked_v2_dataset_is_development_not_holdout():
    cases = load_agent_dataset(DATASET)
    _, components = load_understanding_cases(COMPONENT_DATASET, "development")
    inputs = {case.id: case.input.message for case in components}
    assert len(cases) == 13
    assert {case.split for case in cases} == {"development"}
    linked_ids = set()
    for case in cases:
        for tag in case.tags:
            if tag.startswith("component:"):
                component_id = tag.split(":", 1)[1]
                linked_ids.add(component_id)
                assert any(
                    turn.message == inputs[component_id] for turn in case.turns
                )
    assert len(linked_ids) == 11
    assert {
        "semantic_slot_recovery",
        "semantic_switch_confirmation",
        "slot_overwrite_confirmation",
    } <= {case.category for case in cases}


def test_scripted_model_to_real_v2_workflow_with_every_message_replayed(
    tmp_path,
    monkeypatch,
):
    device_questions = []
    execute = DevicePriceTool.execute

    async def capture_question(tool, question):
        device_questions.append(question)
        return await execute(tool, question)

    monkeypatch.setattr(DevicePriceTool, "execute", capture_question)
    provider = ScriptedProvider()
    report, wire = asyncio.run(_run(tmp_path, provider))
    failed = {
        case.case.id: agent_case_checks(case)
        for case in report.results
        if not agent_case_checks(case)["passed"]
    }
    assert failed == {}, json.dumps(
        {
            case.case.id: [
                turn.observation.model_dump(mode="json") for turn in case.turns
            ]
            for case in report.results
            if case.case.id in failed
        },
        ensure_ascii=False,
    )
    assert report.summary["quality_gate"]["passed"] is True
    assert report.summary["routing"]["wrong_tool_rate"] == 0
    assert report.summary["turns"]["api_error_rate"] == 0
    assert wire.replays == sum(len(case.case.turns) for case in report.results)
    assert len(provider.calls) == 9
    expected_calls = Counter({message: 1 for message in WIRE_FIXTURES})
    expected_calls["我寄出去的那件东西，眼下在什么地方？"] = 3
    assert Counter(provider.calls) == expected_calls
    assert not provider.unexpected and provider.closed
    assert device_questions == ["想入手苹果十六代 Pro，预算大概得准备多少？"]
    # The invented number is never exposed as a result or user-visible slot.
    assert "9999999999999" not in report.model_dump_json()
    output = write_agent_report(report, tmp_path / "reports")
    assert (output / "run.json").is_file()


@pytest.mark.parametrize("failure", ["rate_limit", "invalid_schema"])
def test_provider_failure_remains_visible_to_v2_evaluation(tmp_path, failure):
    provider = ScriptedProvider(failure)
    report, wire = asyncio.run(
        _run(tmp_path, provider, load_agent_dataset(DATASET)[:1])
    )
    assert not report.summary["quality_gate"]["passed"]
    case = report.results[0]
    assert not agent_case_checks(case)["passed"]
    observation = case.turns[0].observation
    assert observation.status == "ok" and observation.phase == "handoff"
    assert observation.result is None
    assert len(provider.calls) == 1 and wire.replays == 1
    assert provider.closed and not provider.unexpected


@pytest.mark.parametrize(
    "case_id,followup",
    [
        ("qu-v2-tracking", "1234567890123"),
        ("qu-v2-delivery-staged", "从北京寄到上海"),
        ("qu-v2-postage-staged", "从北京寄到上海2公斤"),
    ],
)
def test_semantic_interrupt_survives_app_recreation_without_second_model_call(
    tmp_path, case_id, followup
):
    case = next(
        case for case in load_agent_dataset(DATASET) if case.id == case_id
    )
    database = tmp_path / "restart.db"
    first_provider = ScriptedProvider()
    payload = {"message": case.turns[0].message}
    headers = {"Idempotency-Key": "phase5d-initial"}
    with TestClient(
        create_demo_app(
            database_path=database,
            settings=_settings(),
            query_model_transport=first_provider,
        )
    ) as client:
        response = client.post(
            "/v2/agent/messages", json=payload, headers=headers
        )
        assert response.status_code == 200
        waiting = response.json()
        assert waiting["phase"] == "waiting_user"
    assert first_provider.closed and len(first_provider.calls) == 1
    next_provider = ScriptedProvider()
    with TestClient(
        create_demo_app(
            database_path=database,
            settings=_settings(),
            query_model_transport=next_provider,
        )
    ) as client:
        replay = client.post(
            "/v2/agent/messages", json=payload, headers=headers
        )
        state = replay.json()
        assert state.pop("request_id") != waiting.pop("request_id")
        assert state == waiting
        body = {
            "conversation_id": waiting["conversation_id"],
            "message": followup,
        }
        second = client.post(
            "/v2/agent/messages",
            json=body,
            headers={"Idempotency-Key": "phase5d-resume"},
        )
        assert second.status_code == 200
        assert second.json()["phase"] == "completed"
        assert second.json()["result"]["type"] == case.turns[0].expected_intent
        assert second.json()["result"]["status"] == "success"
        again = client.post(
            "/v2/agent/messages",
            json=body,
            headers={"Idempotency-Key": "phase5d-resume"},
        )
        state, repeated = second.json(), again.json()
        assert state.pop("request_id") != repeated.pop("request_id")
        assert state == repeated
    assert not next_provider.calls and next_provider.closed


def write_fixture_evidence(output_dir: Path) -> tuple[Path, bool]:
    """Explicit offline fixture entry, with no way to enable a live transport."""
    with tempfile.TemporaryDirectory(prefix="spb-phase5d-") as directory:
        provider = ScriptedProvider()
        report, _ = asyncio.run(_run(Path(directory), provider))
        output = write_agent_report(report, output_dir)
    return output, report.summary["quality_gate"]["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Phase 5D 离线 V2 fixture 报告，不调用付费模型"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output, passed = write_fixture_evidence(args.output_dir)
    print(
        json.dumps(
            {
                "report_dir": str(output),
                "quality_gate_passed": passed,
                "paid_model_requests": 0,
            }
        )
    )
    raise SystemExit(0 if passed else 3)
