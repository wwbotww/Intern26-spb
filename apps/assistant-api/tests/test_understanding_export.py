from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from spb_assistant_api.adapters.deepseek_understanding import (
    DeepSeekQueryUnderstandingModel,
)
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.understanding_export import (
    ExportRequest,
    export_understanding,
    main,
)
from spb_eval.understanding_dataset import (
    build_requests,
    load_observations,
    load_understanding_cases,
)
from spb_eval.understanding_metrics import (
    calculate_understanding_metrics,
    slot_fingerprints,
)


def _request(*messages):
    return ExportRequest.model_validate(
        {
            "schema_version": "qu-requests-v1",
            "dataset_sha256": "a" * 64,
            "split": "development",
            "cases": [
                {"id": f"case-{index}", "input": {"message": message}}
                for index, message in enumerate(messages)
            ],
        }
    )


def _settings(**overrides):
    return AssistantSettings(
        _env_file=None,
        **{
            "query_model_enabled": True,
            "query_model_api_key": "fixture-export-key",
            "query_model_base_url": "https://model.example.test",
            **overrides,
        },
    )


def _completion():
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": '{"selected_intent":"tracking","candidates":[{"intent":"tracking","score":0.9}]}',
                    "reasoning_content": "private-provider-reasoning",
                },
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "total_tokens": 15,
        },
    }


class Transport(httpx.MockTransport):
    closed = False

    async def aclose(self):
        self.closed = True
        await super().aclose()


def _load_output(tmp_path, stream, request):
    path = tmp_path / "observations.jsonl"
    path.write_text(stream.getvalue(), encoding="utf-8")
    return load_observations(path, request.model_dump(mode="json"))


def test_rules_export_never_loads_settings_or_transmits_queries(
    tmp_path, monkeypatch
):
    def forbidden_settings():
        raise AssertionError("Rules must not read any credentials")

    monkeypatch.setattr(
        "spb_assistant_api.understanding_export.AssistantSettings",
        forbidden_settings,
    )
    request = _request(
        "查邮件 1234567890123",
        "北京寄上海500克邮费",
        "我寄出的东西如今在哪个中转站？",
    )
    stream = io.StringIO()
    result = asyncio.run(export_understanding(request, stream))
    manifest, observations = _load_output(tmp_path, stream, request)
    assert result == {"cases": 3, "model_calls": 0, "errors": 0, "skipped": 0}
    assert manifest.mode == "rules" and manifest.transport == "none"
    assert observations[
        "case-0"
    ].prediction.slot_fingerprints == slot_fingerprints(
        {"mail_no": "1234567890123"}
    )
    assert observations[
        "case-1"
    ].prediction.slot_fingerprints == slot_fingerprints(
        {"origin": "北京市", "destination": "上海市", "weight_kg": "0.5"}
    )
    assert observations["case-2"].prediction.needs_model_fallback
    for value in (
        "1234567890123",
        "北京市",
        "上海市",
        "中转站",
        "original_query",
        "normalized_query",
    ):
        assert value not in stream.getvalue()


@pytest.mark.parametrize(
    "mode,allowed,budget",
    [
        ("hybrid", False, 1),
        ("hybrid", True, 0),
        ("hybrid", True, -1),
        ("rules", True, 1),
        ("hybrid", True, True),
    ],
)
def test_paid_mode_requires_explicit_authorization_and_integer_budget(
    mode, allowed, budget, monkeypatch
):
    def forbidden_settings():
        raise AssertionError("No credentials before authorization")

    monkeypatch.setattr(
        "spb_assistant_api.understanding_export.AssistantSettings",
        forbidden_settings,
    )
    stream = io.StringIO()
    with pytest.raises(ValueError):
        asyncio.run(
            export_understanding(
                _request("未知问法"),
                stream,
                mode=mode,
                allow_live_model=allowed,
                max_model_calls=budget,
            )
        )
    assert not stream.getvalue()


def test_hybrid_budget_skips_only_model_work_and_continues_rules(tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_completion())

    transport = Transport(handler)
    request = _request(
        "我寄出的东西如今在哪个中转站？",
        "另一个包裹现在停在什么地方？",
        "查邮件1234567890123",
        "取消",
    )
    stream = io.StringIO()
    result = asyncio.run(
        export_understanding(
            request,
            stream,
            mode="hybrid",
            allow_live_model=True,
            max_model_calls=1,
            settings=_settings(),
            transport=transport,
        )
    )
    manifest, observations = _load_output(tmp_path, stream, request)
    assert len(calls) == 1 and transport.closed
    assert result == {"cases": 4, "model_calls": 1, "errors": 0, "skipped": 1}
    assert manifest.transport == "test_transport"
    assert observations["case-0"].prediction.source == "model"
    assert observations["case-0"].prediction.missing_slots == ["mail_no"]
    assert observations["case-0"].model_call.total_tokens == 15
    assert observations["case-1"].status == "skipped"
    assert observations["case-1"].prediction is None
    assert (
        observations["case-2"].status == observations["case-3"].status == "ok"
    )
    assert observations["case-2"].model_call.outcome == "not_called"
    assert calls[0]["messages"][1]["content"] == request.cases[0].input.message
    assert "fixture-export-key" not in stream.getvalue()
    assert "private-provider-reasoning" not in stream.getvalue()


@pytest.mark.parametrize("failure", ["timeout", "schema"])
def test_model_failures_retain_unknown_usage_and_safe_fallback(
    tmp_path, failure
):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if failure == "timeout":
            await asyncio.sleep(0.2)
        body = _completion()
        body["choices"][0]["message"]["content"] = "private-invalid-json"
        return httpx.Response(200, json=body)

    transport = Transport(handler)
    request, stream = _request("写一首关于秋天的诗"), io.StringIO()
    asyncio.run(
        export_understanding(
            request,
            stream,
            mode="hybrid",
            allow_live_model=True,
            max_model_calls=1,
            settings=_settings(
                query_model_timeout_seconds=0.05 if failure == "timeout" else 1
            ),
            transport=transport,
        )
    )
    _, observations = _load_output(tmp_path, stream, request)
    observation = observations["case-0"]
    assert calls == 1 and transport.closed
    assert (
        observation.status == "ok" and observation.prediction.source == "rules"
    )
    assert observation.model_call.outcome == "failure"
    assert observation.model_call.total_tokens == (
        None if failure == "timeout" else 15
    )
    assert observation.model_call.failure_code == (
        "query_model_deadline_exceeded"
        if failure == "timeout"
        else "query_model_schema_invalid"
    )
    assert "private-invalid-json" not in stream.getvalue()


def test_cancelled_export_flushes_completed_rows_and_closes_provider(tmp_path):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError()
        return httpx.Response(200, json=_completion())

    transport = Transport(handler)
    request, stream = (
        _request("包裹眼下在何处？", "包裹现在停在哪个中转站？"),
        io.StringIO(),
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            export_understanding(
                request,
                stream,
                mode="hybrid",
                allow_live_model=True,
                max_model_calls=2,
                settings=_settings(),
                transport=transport,
            )
        )
    _, observations = _load_output(tmp_path, stream, request)
    assert list(observations) == ["case-0"]
    assert calls == 2 and transport.closed


def test_producer_contract_rejects_gold_and_cli_refuses_overwrite(
    tmp_path, capsys
):
    request = _request("你好")
    invalid = request.model_dump(mode="json")
    invalid["cases"][0]["gold"] = {"intent": "tracking"}
    with pytest.raises(ValidationError):
        ExportRequest.model_validate(invalid)
    requests, output = tmp_path / "requests.json", tmp_path / "output.jsonl"
    requests.write_text(request.model_dump_json(), encoding="utf-8")
    args = ["--requests", str(requests), "--output", str(output)]
    assert main(args) == 0
    original = output.read_text()
    assert main(args) == 2
    assert output.read_text() == original
    assert "你好" not in capsys.readouterr().out


def test_observer_failure_never_changes_model_behavior():
    observed = []

    def broken_observer(measurement):
        observed.append(dict(measurement))
        raise RuntimeError("observer-is-optional")

    async def run():
        model = DeepSeekQueryUnderstandingModel(
            base_url="https://model.example.test",
            api_key="fixture-observer-key",
            model="test-model",
            call_observer=broken_observer,
            transport=Transport(
                lambda _: httpx.Response(200, json=_completion())
            ),
        )
        try:
            result = await model.classify(
                message="private-query",
                prompt="private-prompt",
                prompt_version="test-v1",
            )
            assert result["selected_intent"] == "tracking"
        finally:
            await model.close()

    asyncio.run(run())
    assert len(observed) == 1 and observed[0]["total_tokens"] == 15
    for value in (
        "private-query",
        "private-prompt",
        "fixture-observer-key",
        "private-provider-reasoning",
    ):
        assert value not in json.dumps(observed)


def test_full_development_rules_export_is_independently_scorable(tmp_path):
    root = Path(__file__).resolve().parents[3]
    dataset_hash, cases = load_understanding_cases(
        root / "eval/datasets/query-understanding-development-v1.jsonl",
        "development",
    )
    request = ExportRequest.model_validate(build_requests(dataset_hash, cases))
    stream = io.StringIO()
    result = asyncio.run(export_understanding(request, stream))
    _, observations = _load_output(tmp_path, stream, request)
    summary, _ = calculate_understanding_metrics(cases, observations)
    assert result["model_calls"] == 0 and len(observations) == 48
    assert summary["intent"]["macro_f1"] == pytest.approx(0.7068117068117069)
    assert summary["slots"]["micro_f1"] == 0.96
    assert summary["model"]["rules_fallback_eligible"] == 20
    assert not summary["quality_gate"]["passed"]
