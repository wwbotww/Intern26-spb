from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from spb_assistant_api import configured_agent
from spb_assistant_api.adapters.postal_tracking import PostalTrackingGateway
from spb_assistant_api.api.app import create_app
from spb_assistant_api.domain.models import QueryMode, ToolResult, ToolStatus
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.tools.unavailable import UnavailableTool


MAIL = "1234567890123"
AUTH = {"Authorization": "Bearer synthetic-t3-a"}
AUTH_B = {"Authorization": "Bearer synthetic-t3-b"}
FIXTURES = Path(__file__).parent / "fixtures" / "postal_tracking"


def fixture(name="success.json"):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def settings(tmp_path, **changes):
    return AssistantSettings(**{
        "agent_enabled": True,
        "agent_database_path": str(tmp_path / "agent.db"),
        "auth_enabled": True, "api_keys": "synthetic-t3-a,synthetic-t3-b",
        "rate_limit_enabled": False, "query_model_enabled": False,
        "tracking_enabled": True,
        "tracking_base_url": "https://postal.example.test/api",
        "tracking_send_id": "SYNTHETIC", "tracking_msg_kind": "SYNTHETIC_TRACE",
        "tracking_signing_system_id": "synthetic-t3-secret",
        "tracking_expected_response_receive_id": "SYNTHETIC",
        "tracking_timezone": "Asia/Shanghai",
        "tracking_profile": "postal-tracking-doc-2019-v1",
        **changes,
    })


def unavailable_tools():
    return {
        QueryMode.POLICY: UnavailableTool("policy_knowledge"),
        QueryMode.DEVICE_PRICE: UnavailableTool("device_price"),
    }


class Transport(httpx.MockTransport):
    def __init__(self, handler):
        super().__init__(handler)
        self.closed = 0

    async def aclose(self):
        self.closed += 1
        await super().aclose()


def install_transport(monkeypatch, handler):
    transports = []
    gateways = []

    def gateway(config, **kwargs):
        transport = Transport(handler)
        transports.append(transport)
        instance = PostalTrackingGateway(config, transport=transport)
        gateways.append(instance)
        return instance

    monkeypatch.setattr(configured_agent, "PostalTrackingGateway", gateway)
    return transports, gateways


def client_for(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://assistant.test",
    )


async def message(client, key, body, auth=AUTH):
    return await client.post(
        "/v2/agent/messages", headers={**auth, "Idempotency-Key": key}, json=body,
    )


@pytest.mark.parametrize("changes", [
    {"auth_enabled": False}, {"api_keys": ""},
    {"agent_database_path": "relative.db"}, {"agent_database_path": ":memory:"},
    {"agent_database_path": ""}, {"agent_enabled": False},
])
def test_controlled_configuration_requires_auth_and_explicit_store(tmp_path, changes):
    with pytest.raises(ValidationError):
        settings(tmp_path, **changes)


@pytest.mark.parametrize("changes", [
    {"tracking_base_url": "http://private-canary.test"},
    {"tracking_base_url": "https://private-canary.test?secret=private-canary"},
    {"tracking_path": "../private-canary"},
    {"tracking_signing_system_id": ""}, {"tracking_send_id": ""},
    {"tracking_expected_response_receive_id": ""},
    {"tracking_timezone": ""}, {"tracking_profile": ""},
    {"tracking_profile": "unknown-profile"},
])
def test_invalid_tracking_configuration_fails_before_startup_without_input_echo(tmp_path, changes):
    with pytest.raises(ValueError) as caught:
        create_app(settings=settings(tmp_path, **changes), tools=unavailable_tools())
    assert "private-canary" not in str(caught.value)
    assert "synthetic-t3-secret" not in str(caught.value)
    assert not (tmp_path / "agent.db").exists()


def test_default_stays_v1_and_disabled_tracking_does_not_construct_gateway(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled dependency constructed")

    monkeypatch.setattr(configured_agent, "PostalTrackingGateway", forbidden)
    default = create_app(settings=AssistantSettings(), tools=unavailable_tools())
    assert not any(path.startswith("/v2") for path in default.openapi()["paths"])

    async def scenario():
        app = create_app(settings=settings(tmp_path, tracking_enabled=False), tools=unavailable_tools())
        async with app.router.lifespan_context(app), client_for(app) as client:
            capabilities = (await client.get("/v2/agent/capabilities", headers=AUTH)).json()
            assert all(not item["available"] for item in capabilities)
            ready = await client.get("/v2/agent/health/ready")
            assert ready.status_code == 503
            assert ready.json()["checks"]["capability.tracking"] == "disabled"

    asyncio.run(scenario())


@pytest.mark.parametrize("empty", [False, True])
def test_auto_composition_json_sse_replay_and_fresh_query(tmp_path, monkeypatch, empty):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=fixture("empty.json" if empty and len(calls) == 1 else "success.json"))

    transports, gateways = install_transport(monkeypatch, handler)

    async def scenario():
        app = create_app(settings=settings(tmp_path), tools=unavailable_tools())
        assert calls == []  # App construction must not issue paid queries.
        async with app.router.lifespan_context(app), client_for(app) as client:
            assert (await client.get("/v2/agent/capabilities")).status_code == 401
            capabilities = (await client.get("/v2/agent/capabilities", headers=AUTH)).json()
            assert [item["intent"] for item in capabilities if item["available"]] == ["tracking"]
            ready = await client.get("/v2/agent/health/ready")
            assert ready.status_code == 200 and calls == []
            body = {"message": f"查邮件 {MAIL}"}
            first = await message(client, "first", body)
            assert first.status_code == 200
            result = first.json()["result"]
            assert result["status"] == ("no_match" if empty else "success")
            source = result["provenance"][0]
            assert source["source_type"] == "external_api"
            assert source["source_profile"] == "postal-tracking-doc-2019-v1"
            assert source["queried_at"] and source["history_completeness"] == "unknown"
            replay = await message(client, "first", {**body, "stream": True})
            assert replay.status_code == 200
            events = [json.loads(block.split("\ndata: ", 1)[1]) for block in replay.text.split("\n\n") if block.startswith("event: done\n")]
            assert events[0]["response"]["result"] == result
            assert len(calls) == 1
            fresh = await message(client, "fresh", {
                "conversation_id": first.json()["conversation_id"], "message": f"再查邮件 {MAIL}",
            })
            assert fresh.json()["result"]["status"] == "success"
            assert fresh.json()["result"]["provenance"][0]["queried_at"] != source["queried_at"]
            assert len(calls) == 2
            for private in ("synthetic-t3-secret", "postal.example.test", "operatorName", "dataDigest", "query_id"):
                assert private not in fresh.text + replay.text + ready.text
        assert app.state.agent_api is None
        assert await gateways[0].readiness() == "not_ready"

    asyncio.run(scenario())
    assert [transport.closed for transport in transports] == [1]


def test_shutdown_restart_waiting_resume_owner_and_delete(tmp_path, monkeypatch):
    calls = []
    transports, _ = install_transport(monkeypatch, lambda request: (
        calls.append(request) or httpx.Response(200, json=fixture())
    ))

    async def scenario():
        app = create_app(settings=settings(tmp_path), tools=unavailable_tools())
        async with app.router.lifespan_context(app), client_for(app) as client:
            waiting = await message(client, "start", {"message": "查轨迹"})
            assert waiting.json()["phase"] == "waiting_user"
            conversation = waiting.json()["conversation_id"]
            assert calls == []
        second = create_app(settings=settings(tmp_path), tools=unavailable_tools())
        async with second.router.lifespan_context(second), client_for(second) as client:
            body = {"conversation_id": conversation, "message": MAIL}
            denied = await message(client, "resume", body, auth=AUTH_B)
            assert denied.status_code == 404 and calls == []
            resumed = await message(client, "resume", body)
            assert resumed.json()["phase"] == "completed"
            assert len(calls) == 1
            assert (await message(client, "resume", body)).json()["result"] == resumed.json()["result"]
            assert len(calls) == 1
            assert (await client.delete(f"/v2/agent/conversations/{conversation}", headers=AUTH_B)).status_code == 404
            assert (await client.delete(f"/v2/agent/conversations/{conversation}", headers=AUTH)).status_code == 204
            # Deletion deliberately shares the not-found surface with owner denial.
            assert (await message(client, "resume", body)).status_code == 404

    asyncio.run(scenario())
    assert [transport.closed for transport in transports] == [1, 1]


@pytest.mark.parametrize("intent", ["delivery_time", "postage"])
def test_missing_shipping_contract_is_unavailable_without_provider_calls(tmp_path, monkeypatch, intent):
    calls = []
    install_transport(monkeypatch, lambda request: calls.append(request))

    async def scenario():
        app = create_app(settings=settings(tmp_path), tools=unavailable_tools())
        async with app.router.lifespan_context(app), client_for(app) as client:
            response = await message(client, "unsupported", {
                "explicit_intent": intent,
                "message": "查时限" if intent == "delivery_time" else "查资费",
            })
            assert response.status_code == 200, response.text
            assert response.json()["phase"] == "handoff"
            assert response.json()["result"] is None and calls == []

    asyncio.run(scenario())


class BorrowedTool:
    def __init__(self, name):
        self.name = name
        self.starts = self.stops = self.calls = 0

    async def initialize(self):
        self.starts += 1

    async def close(self):
        self.stops += 1

    def readiness(self):
        return "ready" if self.starts > self.stops else "not_ready"

    async def execute(self, question):
        self.calls += 1
        return ToolResult(tool=self.name, status=ToolStatus.NO_MATCH, answer="合成测试未匹配资料")


def test_v1_and_v2_borrow_the_same_initialized_tools(tmp_path):
    policy, price = BorrowedTool("policy_knowledge"), BorrowedTool("device_price")

    async def scenario():
        app = create_app(settings=settings(tmp_path, tracking_enabled=False), tools={
            QueryMode.POLICY: policy, QueryMode.DEVICE_PRICE: price,
        })
        async with app.router.lifespan_context(app), client_for(app) as client:
            for mode, tool in (("policy", policy), ("device_price", price)):
                v1 = await client.post("/v1/chat", headers=AUTH, json={"mode": mode, "question": "合成查询"})
                assert v1.status_code == 200
                v2 = await message(client, mode, {"message": "合成查询", "explicit_intent": mode})
                assert v2.json()["result"]["status"] == "no_match"
                assert tool.calls == 2 and tool.starts == 1 and tool.stops == 0
            ready = (await client.get("/v2/agent/health/ready")).json()
            assert ready["checks"]["capability.policy"] == "ready"
        assert policy.stops == price.stops == 1

    asyncio.run(scenario())


def test_later_startup_failure_closes_owned_gateway(tmp_path, monkeypatch):
    transports, _ = install_transport(monkeypatch, lambda request: None)
    # aiosqlite must not silently create a missing parent or use a temporary DB.
    app = create_app(settings=settings(tmp_path, agent_database_path=str(tmp_path / "missing" / "store.db")), tools=unavailable_tools())

    async def scenario():
        with pytest.raises(sqlite3.OperationalError):
            async with app.router.lifespan_context(app):
                pytest.fail("startup should fail")
        assert app.state.agent_api is None

    asyncio.run(scenario())
    assert [transport.closed for transport in transports] == [1]


@pytest.mark.parametrize("kind,retryable,category", [
    ("timeout", True, "upstream_timeout"),
    ("429", True, "upstream_rate_limited"),
    ("503", True, "upstream_unavailable"),
    ("schema", False, "contract_violation"),
    ("recipient", False, "contract_violation"),
    ("business", False, "upstream_unavailable"),
])
def test_mock_failure_matrix_through_v2_has_bounded_calls_and_no_fabricated_result(tmp_path, monkeypatch, kind, retryable, category):
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("private-canary", request=request)
        if kind in {"429", "503"}:
            return httpx.Response(int(kind), text="private-canary", headers={"Retry-After": "0"})
        body = fixture()
        if kind == "schema":
            body["responseState"] = "true"
        elif kind == "recipient":
            body["receiveID"] = "WRONG"
        else:
            body.update(responseState=False, errorDesc="private-canary")
        return httpx.Response(200, json=body)

    install_transport(monkeypatch, handler)

    async def scenario():
        app = create_app(settings=settings(tmp_path), tools=unavailable_tools())
        async with app.router.lifespan_context(app), client_for(app) as client:
            body = {"message": f"查轨迹 {MAIL}"}
            response = await message(client, "failure", body)
            assert response.status_code == 200
            result = response.json()
            assert result["phase"] == "failed" and result["result"] is None
            assert result["failure"]["category"] == category
            assert result["failure"]["retryable"] is retryable
            assert len(calls) == (2 if retryable else 1)
            replay = await message(client, "failure", body)
            assert replay.json()["failure"] == result["failure"]
            assert len(calls) == (2 if retryable else 1)
            assert "private-canary" not in replay.text

    asyncio.run(scenario())


def test_readiness_reports_open_contract_circuit_without_probe_requests(tmp_path, monkeypatch):
    calls = []
    install_transport(monkeypatch, lambda request: calls.append(request) or httpx.Response(200, json={}))

    async def scenario():
        app = create_app(settings=settings(tmp_path), tools=unavailable_tools())
        async with app.router.lifespan_context(app), client_for(app) as client:
            for index in range(3):
                assert (await message(client, f"bad-{index}", {"message": f"查邮件 {MAIL}"})).json()["phase"] == "failed"
            ready = await client.get("/v2/agent/health/ready")
            assert ready.status_code == 503
            assert ready.json()["checks"]["capability.tracking"] == "degraded"
            assert len(calls) == 3
            response = await message(client, "open", {"message": f"查邮件 {MAIL}"})
            assert response.json()["failure"]["code"] == "capability_circuit_open"
            assert len(calls) == 3

    asyncio.run(scenario())
