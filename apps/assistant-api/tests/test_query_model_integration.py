from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from spb_assistant_api.adapters.deepseek_understanding import (
    DeepSeekQueryUnderstandingModel,
)
from spb_assistant_api.agent_demo import DEMO_MAIL_NO, create_demo_app
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.observability.context import (
    bind_request_id,
    reset_request_id,
)
from spb_assistant_api.query_model import create_query_understander
from spb_assistant_api.services.query_understanding import (
    HybridQueryUnderstander,
    StructuredLlmQueryUnderstander,
)
from spb_assistant_api.settings import AssistantSettings

PARAPHRASE = "我寄出去的那件东西，眼下在什么地方？"
SECRET = "fixture-query-model-secret"


def _understanding() -> dict[str, Any]:
    return {
        "selected_intent": "tracking",
        "candidates": [
            {
                "intent": "tracking",
                "score": 0.9,
                "signals": ["semantic_intent"],
            }
        ],
        # An invented value from a schema-valid model must still be discarded.
        "slots": {"intent": "tracking", "mail_no": "9999999999999"},
    }


def _completion(content: str | None = None) -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(_understanding())
                    if content is None
                    else content,
                    "reasoning_content": "private-provider-reasoning",
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
        },
    }


def _model(handler, **kwargs) -> DeepSeekQueryUnderstandingModel:
    return DeepSeekQueryUnderstandingModel(
        base_url="https://model.example.test/v1",
        api_key=SECRET,
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def _classify(model: DeepSeekQueryUnderstandingModel):
    return await model.classify(
        message=PARAPHRASE,
        prompt=StructuredLlmQueryUnderstander.prompt,
        prompt_version=StructuredLlmQueryUnderstander.prompt_version,
    )


def test_real_wire_adapter_respects_hybrid_rules_and_sanitizes_logs(
    caplog,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_completion())

    async def scenario() -> None:
        model = _model(handler)
        hybrid = HybridQueryUnderstander(
            model_fallback=StructuredLlmQueryUnderstander(model)
        )
        token = bind_request_id("model-integration-test")
        try:
            rule_result = await hybrid.understand(message=f"查邮件 {DEMO_MAIL_NO}")
            assert rule_result.source == "rules"
            assert requests == []
            result = await hybrid.understand(message=PARAPHRASE)
            assert result.source == "model"
            assert result.selected_intent is Intent.TRACKING
            assert result.prompt_version == "query-understanding-v2"
            assert result.slots is not None and result.slots.mail_no is None
            assert result.missing_slots == ["mail_no"]
            for text in ("取消", "重新开始"):
                await hybrid.understand(message=text)
            await hybrid.understand(
                message="2公斤",
                active_intent=Intent.POSTAGE,
                expected_slots=("weight",),
            )
            await hybrid.understand(message="帮我处理", explicit_intent=Intent.POLICY)
            assert len(requests) == 1
        finally:
            reset_request_id(token)
            await model.close()

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())

    request = requests[0]
    assert str(request.url) == "https://model.example.test/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {SECRET}"
    assert request.headers["X-Request-ID"] == "model-integration-test"
    body = json.loads(request.content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["stream"] is False
    assert body["max_tokens"] == 768
    assert "tools" not in body and "tool_choice" not in body
    assert body["messages"][1] == {"role": "user", "content": PARAPHRASE}
    assert '"additionalProperties":false' in body["messages"][0]["content"]
    assert "JSON Schema:" in body["messages"][0]["content"]
    records = [r for r in caplog.records if r.name == "spb_assistant_api.query_model"]
    assert len(records) == 1 and records[0].outcome == "success"
    assert records[0].total_tokens == 140
    assert records[0].duration_ms >= 0
    logged = repr([r.__dict__ for r in caplog.records])
    for value in (
        SECRET,
        PARAPHRASE,
        DEMO_MAIL_NO,
        "9999999999999",
        "private-provider-reasoning",
    ):
        assert value not in logged


@pytest.mark.parametrize(
    "case,code",
    [
        ("length", "query_model_incomplete_output"),
        ("tool_finish", "query_model_incomplete_output"),
        ("tool_call", "query_model_envelope_invalid"),
        ("missing_choices", "query_model_envelope_invalid"),
        ("extra_choice", "query_model_envelope_invalid"),
        ("wrong_role", "query_model_envelope_invalid"),
        ("empty", "query_model_empty_output"),
        ("invalid_json", "query_model_schema_invalid"),
        ("markdown", "query_model_schema_invalid"),
        ("extra_field", "query_model_schema_invalid"),
        ("unknown_enum", "query_model_schema_invalid"),
        ("duplicate", "query_model_schema_invalid"),
        ("non_finite", "query_model_schema_invalid"),
    ],
)
def test_model_wire_and_domain_contract_fail_closed(case: str, code: str) -> None:
    payload = _completion()
    choice = payload["choices"][0]
    if case == "length":
        choice["finish_reason"] = "length"
    elif case == "tool_finish":
        choice["finish_reason"] = "tool_calls"
    elif case == "tool_call":
        choice["message"]["tool_calls"] = [{"function": {"name": "untrusted"}}]
    elif case == "missing_choices":
        payload = {}
    elif case == "extra_choice":
        payload["choices"].append(choice.copy())
    elif case == "wrong_role":
        choice["message"]["role"] = "user"
    else:
        content = {
            "empty": " ",
            "invalid_json": "not-json private-upstream-text",
            "markdown": '```json\n{"selected_intent":"unknown"}\n```',
            "extra_field": '{"selected_intent":"unknown","tool_name":"unsafe"}',
            "unknown_enum": '{"selected_intent":"unsafe"}',
            "duplicate": '{"selected_intent":"unsafe","selected_intent":"unknown"}',
            "non_finite": '{"selected_intent":"unknown","candidates":[{"intent":"unknown","score":NaN}]}',
        }[case]
        choice["message"]["content"] = content

    async def scenario() -> None:
        model = _model(lambda _: httpx.Response(200, json=payload))
        try:
            with pytest.raises(AgentOperationError) as exc:
                await _classify(model)
            assert exc.value.failure.code == code
            assert exc.value.failure.category.value == "contract_violation"
            assert "private-upstream-text" not in str(exc.value)
            hybrid = HybridQueryUnderstander(
                model_fallback=StructuredLlmQueryUnderstander(model)
            )
            result = await hybrid.understand(message=PARAPHRASE)
            assert result.selected_intent is Intent.UNKNOWN
            assert (
                "model_output_invalid" in result.ambiguities
                or "model_fallback_failed" in result.ambiguities
            )
        finally:
            await model.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,category",
    [
        (401, "upstream_unavailable"),
        (429, "upstream_rate_limited"),
        (503, "upstream_unavailable"),
        (504, "upstream_timeout"),
    ],
)
def test_http_failure_uses_one_attempt_then_rule_fallback(
    status: int, category: str, caplog
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": SECRET + " private-upstream-body"})

    async def scenario() -> None:
        model = _model(handler)
        try:
            hybrid = HybridQueryUnderstander(
                model_fallback=StructuredLlmQueryUnderstander(model)
            )
            result = await hybrid.understand(message=PARAPHRASE)
            assert result.selected_intent is Intent.UNKNOWN
            assert "model_fallback_failed" in result.ambiguities
            assert calls == 1
        finally:
            await model.close()

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())
    record = next(
        r for r in caplog.records if r.name == "spb_assistant_api.query_model"
    )
    assert record.failure_category == category
    assert SECRET not in repr([r.__dict__ for r in caplog.records])
    assert "private-upstream-body" not in caplog.text


def test_total_deadline_bounds_slow_provider_and_releases_capacity() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.Event().wait()
        return httpx.Response(200, json=_completion())

    async def scenario() -> None:
        model = _model(handler, timeout_seconds=0.05, max_concurrency=1)
        try:
            with pytest.raises(AgentOperationError) as exc:
                await _classify(model)
            assert exc.value.failure.code == "query_model_deadline_exceeded"
            assert (await _classify(model))["selected_intent"] == "tracking"
            assert calls == 2
        finally:
            await model.close()

    asyncio.run(scenario())


def test_cancellation_propagates_and_concurrency_is_bounded() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        calls = active = peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls, active, peak
            calls += 1
            active += 1
            peak = max(peak, active)
            try:
                if calls == 1:
                    entered.set()
                    await asyncio.Event().wait()
                await asyncio.sleep(0.005)
                return httpx.Response(200, json=_completion())
            finally:
                active -= 1

        model = _model(handler, timeout_seconds=1, max_concurrency=1)
        try:
            hybrid = HybridQueryUnderstander(
                model_fallback=StructuredLlmQueryUnderstander(model)
            )
            task = asyncio.create_task(hybrid.understand(message=PARAPHRASE))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            results = await asyncio.gather(*(_classify(model) for _ in range(3)))
            assert len(results) == 3 and calls == 4
            assert peak == 1
        finally:
            await model.close()

    asyncio.run(scenario())


class ClosingTransport(httpx.MockTransport):
    closed = False

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


def _settings(**overrides) -> AssistantSettings:
    return AssistantSettings(
        _env_file=None,
        **{
            "query_model_enabled": True,
            "query_model_api_key": SECRET,
            "query_model_base_url": "https://model.example.test",
            **overrides,
        },
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"query_model_api_key": " "},
        {"query_model_name": " "},
        {"query_model_base_url": "https://user:pass@example.test"},
        {"query_model_base_url": "https://example.test/?key=private"},
        {"query_model_base_url": "http://example.test"},
        {"query_model_timeout_seconds": 0},
        {"query_model_max_concurrency": 0},
    ],
)
def test_enabling_provider_requires_valid_explicit_configuration(
    overrides,
) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)


def test_model_factory_is_opt_in_and_closes_resources_on_failure() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_completion())

    async def scenario() -> None:
        disabled_transport = ClosingTransport(handler)
        async with create_query_understander(
            _settings(query_model_enabled=False, query_model_api_key=""),
            transport=disabled_transport,
        ) as u:
            assert (await u.understand(message=PARAPHRASE)).source == "rules"
        assert calls == 0 and not disabled_transport.closed
        enabled_transport = ClosingTransport(handler)
        with pytest.raises(RuntimeError, match="fixture-lifespan-error"):
            async with create_query_understander(
                _settings(), transport=enabled_transport
            ) as u:
                assert (await u.understand(message=PARAPHRASE)).source == "model"
                raise RuntimeError("fixture-lifespan-error")
        assert enabled_transport.closed and calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "intent,message,followup,required",
    [
        ("tracking", PARAPHRASE, DEMO_MAIL_NO, ["mail_no"]),
        (
            "delivery_time",
            "我今天交给寄递员的东西，收件人大概何时能收到？",
            "从北京市寄到上海市",
            ["origin", "destination"],
        ),
        (
            "postage",
            "替我把这个包裹送过去，要花多少银子？",
            "从北京市寄到上海市，2公斤",
            ["origin", "destination", "weight"],
        ),
    ],
)
def test_configured_model_drives_v2_clarification_then_rule_resume(
    tmp_path: Path,
    intent: str,
    message: str,
    followup: str,
    required: list[str],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        understanding = _understanding()
        understanding["selected_intent"] = intent
        understanding["candidates"][0]["intent"] = intent
        if intent != "tracking":
            understanding["slots"] = None
        return httpx.Response(200, json=_completion(json.dumps(understanding)))

    transport = ClosingTransport(handler)
    app = create_demo_app(
        database_path=tmp_path / "model-agent.db",
        settings=_settings(),
        query_model_transport=transport,
    )
    with TestClient(app) as client:
        first = client.post(
            "/v2/agent/messages",
            headers={"Idempotency-Key": "model-first"},
            json={"message": message},
        )
        assert first.status_code == 200
        state = first.json()
        assert state["intent"] == intent and state["phase"] == "waiting_user"
        assert [item["name"] for item in state["required_inputs"]] == required
        assert "9999999999999" not in first.text
        first_replay = client.post(
            "/v2/agent/messages",
            headers={"Idempotency-Key": "model-first"},
            json={"message": message},
        )
        assert first_replay.status_code == 200
        first_replay_state = first_replay.json()
        assert first_replay_state.pop("request_id") != state.pop("request_id")
        assert first_replay_state == state
        payload = {
            "conversation_id": state["conversation_id"],
            "message": followup,
        }
        second = client.post(
            "/v2/agent/messages",
            headers={"Idempotency-Key": "model-resume"},
            json=payload,
        )
        assert second.status_code == 200
        assert second.json()["phase"] == "completed"
        assert second.json()["result"]["type"] == intent
        assert second.json()["result"]["status"] == "success"
        replay = client.post(
            "/v2/agent/messages",
            headers={"Idempotency-Key": "model-resume"},
            json=payload,
        )
        replay_state, second_state = replay.json(), second.json()
        assert replay_state.pop("request_id") != second_state.pop("request_id")
        assert replay_state == second_state
        assert calls == 1
    assert transport.closed


@pytest.mark.parametrize("deadline", [False, True], ids=["unknown", "timeout"])
def test_v2_unknown_and_model_timeout_handoff_without_business_calls(
    tmp_path: Path,
    caplog,
    deadline: bool,
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if deadline:
            await asyncio.sleep(0.2)
        return httpx.Response(
            200,
            json=_completion(
                json.dumps({"selected_intent": "unknown", "candidates": []})
            ),
        )

    transport = ClosingTransport(handler)
    app = create_demo_app(
        database_path=tmp_path / "model-unknown.db",
        settings=_settings(
            query_model_timeout_seconds=0.05 if deadline else 1,
        ),
        query_model_transport=transport,
    )
    with caplog.at_level(logging.INFO), TestClient(app) as client:
        payload = {"message": "写一首关于秋天的诗"}
        headers = {"Idempotency-Key": "model-unknown"}
        first = client.post("/v2/agent/messages", headers=headers, json=payload)
        assert first.status_code == 200
        state = first.json()
        assert state["phase"] == "handoff" and state["intent"] is None
        assert state["result"] is None and state["required_inputs"] == []

        workflow = [
            record
            for record in caplog.records
            if record.name == "spb_assistant_api.workflow_trace"
        ][-1]
        assert workflow.logical_tool_call_count == 0
        understanding = next(
            step["details"] for step in workflow.steps if step["node"] == "understand"
        )
        assert understanding["intent"] == "unknown"
        assert understanding["source"] == ("rules" if deadline else "model")
        model_call = [
            record
            for record in caplog.records
            if record.name == "spb_assistant_api.query_model"
        ][-1]
        assert model_call.outcome == ("failure" if deadline else "success")
        assert model_call.failure_code == (
            "query_model_deadline_exceeded" if deadline else None
        )

        replay = client.post("/v2/agent/messages", headers=headers, json=payload)
        assert replay.status_code == 200
        replay_state = replay.json()
        assert replay_state.pop("request_id") != state.pop("request_id")
        assert replay_state == state
        # The optional dependency cannot prevent deterministic requests.
        direct = client.post(
            "/v2/agent/messages",
            headers={"Idempotency-Key": "rule-after-model-handoff"},
            json={"message": f"查邮件 {DEMO_MAIL_NO}"},
        )
        assert direct.status_code == 200
        assert direct.json()["phase"] == "completed"
        assert direct.json()["result"]["type"] == "tracking"
        assert calls == 1
    assert transport.closed
