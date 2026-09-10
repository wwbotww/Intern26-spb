import asyncio
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from spb_assistant_api.api.app import create_app
from spb_assistant_api.deployment_demo import _SyntheticLegacyTool
from spb_assistant_api.domain.models import QueryMode
from spb_assistant_api.security.browser_session import BrowserSessionConfig, private_http_origin
from spb_assistant_api.storage_paths import init_store

from .test_phase6a3_deployment import PROXY_KEY, SIGNING_KEY, deployment_settings

ORIGIN = "http://10.20.30.40:3000"


@pytest.mark.parametrize("origin", [
    "http://10.20.30.40:3000", "http://172.16.0.1", "http://172.31.255.254:65535",
    "http://192.168.0.1:80", "http://127.0.0.1:13443",
])
def test_private_http_origin_accepts_only_canonical_explicit_addresses(origin):
    assert private_http_origin(origin)


@pytest.mark.parametrize("origin", [
    "http://8.8.8.8:3000", "http://0.0.0.0:3000", "http://169.254.169.254",
    "http://172.15.0.1", "http://172.32.0.1", "http://192.169.0.1",
    "http://10.0.0.256", "http://10.00.0.1", "http://10.0.0.1:0", "http://10.0.0.1:65536",
    "http://10.0.0.1:03000", "http://10.0.0.1/", "http://user@10.0.0.1",
    "http://10.0.0.1?x=1", "http://10.0.0.1#x", "http://intranet.example",
    "http://localhost:3000", "http://[::1]:3000", "https://10.0.0.1:3000",
])
def test_private_http_never_accepts_public_dns_or_ambiguous_origins(origin):
    assert not private_http_origin(origin)


def test_browser_http_exception_requires_explicit_flag_and_separate_cookie():
    config = BrowserSessionConfig(
        public_origin=ORIGIN, proxy_api_key=PROXY_KEY, signing_key=SIGNING_KEY,
        secure=False, private_http_enabled=True,
    )
    assert config.cookie_name == "spb-agent-intranet"
    with pytest.raises(ValueError):
        replace(config, private_http_enabled=False)
    with pytest.raises(ValueError):
        replace(config, secure=True)
    with pytest.raises(ValueError):
        replace(config, public_origin="http://example.com")


def intranet_settings(tmp_path, **changes):
    return deployment_settings(tmp_path, **{
        "deployment_transport_mode": "private-http",
        "agent_browser_private_http_enabled": True,
        "agent_browser_cookie_secure": False,
        "agent_browser_public_origin": ORIGIN,
        "query_model_enabled": False,
        **changes,
    })


@pytest.mark.parametrize("changes", [
    {"deployment_transport_mode": "https"},
    {"agent_browser_private_http_enabled": False},
    {"agent_browser_cookie_secure": True},
    {"agent_browser_public_origin": "http://example.com"},
    {"agent_browser_public_origin": "https://10.20.30.40:3000"},
    {"auth_enabled": False},
])
def test_http_deployment_rejects_partial_opt_in_before_storage(tmp_path, changes):
    with pytest.raises(ValidationError):
        intranet_settings(tmp_path, **changes)
    assert not (tmp_path / "store").exists()


def test_two_core_capabilities_three_unavailable_and_http_owner_isolation(tmp_path, monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError("No live providers in HTTP profile regression")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    asyncio.run(check_intranet_profile(tmp_path))


async def check_intranet_profile(tmp_path):
    init_store(tmp_path / "store")
    calls = []

    class RecordingTool(_SyntheticLegacyTool):
        async def execute(self, question):
            calls.append(self._mode)
            return await super().execute(question)

    app = create_app(settings=intranet_settings(tmp_path), tools={mode: RecordingTool(mode) for mode in QueryMode})
    headers = {"Authorization": "Bearer " + PROXY_KEY, "Origin": ORIGIN}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN, headers=headers) as first, httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN, headers=headers) as second:
            for client in (first, second):
                identity = await client.post("/v2/agent/browser-session", json={})
                assert identity.status_code == 200
                cookie = identity.headers["set-cookie"]
                assert cookie.startswith("spb-agent-intranet=")
                assert "HttpOnly" in cookie and "SameSite=strict" in cookie
                assert "Secure" not in cookie and "Domain=" not in cookie
                client.headers["X-Agent-Session"] = identity.json()["session_ref"]
            assert first.headers["X-Agent-Session"] != second.headers["X-Agent-Session"]
            assert (await first.get("/v2/agent/health/ready")).status_code == 200
            capabilities = (await first.get("/v2/agent/capabilities")).json()
            assert {item["intent"]: item["available"] for item in capabilities} == {
                "policy": True, "device_price": True, "tracking": False,
                "postage": False, "delivery_time": False,
            }
            for intent in ("tracking", "postage", "delivery_time"):
                for message in ("查询", "查邮件 1234567890123，北京寄上海，1.25 公斤"):
                    result = await first.post("/v2/agent/messages", json={"explicit_intent": intent, "message": message}, headers={"Idempotency-Key": intent + str(len(message))})
                    assert result.status_code == 200
                    body = result.json()
                    assert body["phase"] == "handoff" and body["required_inputs"] == []
                    assert body["reply"] == "该查询服务暂不可用，请稍后再试。"
                    assert body["result"] is None
            assert calls == []  # No pointless slot collection or provider fallback.
            for intent, question in (("policy", "快件丢失理赔需要什么材料"), ("device_price", "iPhone 16 Pro 256GB 价格")):
                response = await first.post("/v2/agent/messages", json={"explicit_intent": intent, "message": question}, headers={"Idempotency-Key": intent})
                assert response.status_code == 200 and response.json()["phase"] == "completed"
                replay = await first.post("/v2/agent/messages", json={"explicit_intent": intent, "message": question}, headers={"Idempotency-Key": intent})
                assert replay.json()["result"] == response.json()["result"]
            assert calls == [QueryMode.POLICY, QueryMode.DEVICE_PRICE]
            conversation = response.json()["conversation_id"]
            assert (await second.delete("/v2/agent/conversations/" + conversation)).status_code == 404
            assert (await first.post("/v2/agent/browser-session", json={}, headers={"Origin": "http://10.20.30.41:3000"})).status_code == 403


def test_web_uses_business_unavailability_wording():
    source = (Path(__file__).resolve().parents[3] / "apps/chat-web/src/AgentApp.vue").read_text()
    assert "暂不可用</em>" in source
    assert "当前未装配" not in source
