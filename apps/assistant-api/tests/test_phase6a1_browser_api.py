import asyncio
import json
import time
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from spb_assistant_api.security.browser_session import BrowserSessionManager, SESSION_PATH

from .browser_session_fixture import API_KEY, OTHER_KEY, ORIGIN, app_at, browser_config
from .postage_p3_fixture import CONFIRM, QUERY, app_at as legacy_app


def headers(**extra):
    return {"Authorization": f"Bearer {API_KEY}", "Origin": ORIGIN, **extra}


async def bootstrap(client):
    response = await client.post(SESSION_PATH, json={})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"session_ref", "expires_at"}
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert "Domain=" not in response.headers["set-cookie"]
    client.headers["X-Agent-Session"] = response.json()["session_ref"]
    return response


async def send(client, message=QUERY, conversation=None, key=None, **payload):
    return await client.post("/v2/agent/messages", json={
        "message": message, "conversation_id": conversation, **payload,
    }, headers={"Idempotency-Key": key or str(uuid4())})


def test_two_cookie_jars_owner_spoof_isolation_and_service_client(tmp_path):
    asyncio.run(check_two_cookie_jars(tmp_path))


async def check_two_cookie_jars(tmp_path):
    transports = []
    app = app_at(tmp_path / "agent.db", transports)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN, headers=headers()) as a, httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN, headers=headers()) as b:
            await bootstrap(a)
            await bootstrap(b)
            assert a.headers["X-Agent-Session"] != b.headers["X-Agent-Session"]
            first = (await send(a, key="same-creation-key")).json()
            second = (await send(b, key="same-creation-key")).json()
            assert first["conversation_id"] != second["conversation_id"]
            conversation = first["conversation_id"]
            b.headers.update({"X-User-ID": "browser:" + a.headers["X-Agent-Session"], "X-Agent-Owner": "browser:" + a.headers["X-Agent-Session"], "X-Forwarded-User": "attacker"})
            for stream in (False, True):
                denied = await send(b, CONFIRM, conversation, stream=stream)
                if stream:
                    assert denied.status_code == 200 and '"code":"conversation_not_available"' in denied.text
                    assert "event: result" not in denied.text and "event: done" not in denied.text
                else:
                    assert denied.status_code == 404, denied.text
            assert (await b.delete(f"/v2/agent/conversations/{conversation}")).status_code == 404
            assert transports[0].calls == []
            result = await send(a, CONFIRM, conversation, key="approve", stream=True)
            assert result.status_code == 200 and '"amount":"12.30"' in result.text.replace(" ", "")
            replay = await send(a, CONFIRM, conversation, key="approve")
            assert replay.status_code == 200 and len(transports[0].calls) == 1
            assert (await a.delete(f"/v2/agent/conversations/{conversation}")).status_code == 204
            # Other trusted API service keys retain legacy owner behavior without cookies / Origin.
            b.headers.clear()
            b.headers["Authorization"] = f"Bearer {OTHER_KEY}"
            assert (await send(b)).status_code == 200
            assert (await b.post(SESSION_PATH, json={})).status_code == 403
    assert transports[0].closed


def test_bootstrap_refresh_reset_restart_and_stale_tab(tmp_path):
    transports = []
    app = app_at(tmp_path / "agent.db", transports)
    with TestClient(app, base_url=ORIGIN, headers=headers()) as client:
        first = client.post(SESSION_PATH, json={})
        ref = first.json()["session_ref"]
        client.headers["X-Agent-Session"] = ref
        started = client.post("/v2/agent/messages", json={"message": QUERY}, headers={"Idempotency-Key": "create"}).json()
        saved_cookies = client.cookies.jar
        again = client.post(SESSION_PATH, json={})
        assert again.json() == first.json()
        assert "set-cookie" not in again.headers
    # Same SQLite + same signing key: restart preserves ownership and command review.
    with TestClient(app, base_url=ORIGIN, headers=headers(), cookies=saved_cookies) as client:
        assert client.post(SESSION_PATH, json={}).json() == first.json()
        client.headers["X-Agent-Session"] = ref
        done = client.post("/v2/agent/messages", json={"message": CONFIRM, "conversation_id": started["conversation_id"]}, headers={"Idempotency-Key": "finish"})
        assert done.status_code == 200 and len(transports[-1].calls) == 1
        reset = client.post(SESSION_PATH, json={"reset": True})
        assert reset.json()["session_ref"] != ref
        stale = client.get("/v2/agent/capabilities")
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "browser_session_changed"
        client.headers["X-Agent-Session"] = reset.json()["session_ref"]
        assert client.delete(f"/v2/agent/conversations/{started['conversation_id']}").status_code == 404


def test_delayed_old_bootstrap_does_not_overwrite_reset_cookie(tmp_path):
    with TestClient(app_at(tmp_path / "agent.db", []), base_url=ORIGIN, headers=headers()) as client:
        first = client.post(SESSION_PATH, json={})
        old_cookie = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
        reset = client.post(SESSION_PATH, json={"reset": True})
        assert reset.json()["session_ref"] != first.json()["session_ref"]
        # Simulates an old in-flight verification finishing after an explicit reset.
        delayed = client.post(SESSION_PATH, json={}, headers={"Cookie": old_cookie})
        assert delayed.json() == first.json()
        assert "set-cookie" not in delayed.headers
        current = client.post(SESSION_PATH, json={})
        assert current.json() == reset.json()


@pytest.mark.parametrize("case,status", [
    ("missing_auth", 401), ("wrong_role", 403), ("missing_origin", 403),
    ("wrong_origin", 403), ("cross_site", 403), ("duplicate_origin", 400),
    ("extra_owner", 422), ("bad_reset", 422), ("tampered", 401), ("expired", 401),
])
def test_bootstrap_failure_matrix(tmp_path, case, status):
    request_headers = list(headers().items())
    body = {}
    if case == "missing_auth":
        request_headers = [(k, v) for k, v in request_headers if k != "Authorization"]
    if case == "wrong_role":
        request_headers = [("Authorization", f"Bearer {OTHER_KEY}"), ("Origin", ORIGIN)]
    if case == "missing_origin":
        request_headers = request_headers[:1]
    if case == "wrong_origin":
        request_headers = [request_headers[0], ("Origin", "https://evil.invalid")]
    if case == "cross_site":
        request_headers += [("Sec-Fetch-Site", "cross-site")]
    if case == "duplicate_origin":
        request_headers += [("Origin", ORIGIN)]
    if case == "extra_owner":
        body = {"owner_id": "attacker"}
    if case == "bad_reset":
        body = {"reset": "true"}
    if case == "tampered":
        request_headers += [("Cookie", "spb-agent-local=tampered-secret")]
    if case == "expired":
        manager = BrowserSessionManager(browser_config(), clock=lambda: time.time() - 2000)
        request_headers += [("Cookie", "spb-agent-local=" + manager.mint().token)]
    with TestClient(app_at(tmp_path / "agent.db", []), base_url=ORIGIN) as client:
        response = client.post(SESSION_PATH, json=body, headers=request_headers)
        assert response.status_code == status, response.text
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        assert "tampered-secret" not in response.text


def test_browser_business_endpoints_require_cookie_ref_and_origin(tmp_path):
    with TestClient(app_at(tmp_path / "agent.db", []), base_url=ORIGIN, headers=headers()) as client:
        assert client.get("/v2/agent/capabilities").status_code == 401
        boot = client.post(SESSION_PATH, json={})
        assert client.get("/v2/agent/capabilities").status_code == 409
        client.headers["X-Agent-Session"] = boot.json()["session_ref"]
        assert client.get("/v2/agent/capabilities").status_code == 200
        # Safe fetch can omit Origin; unsafe actions cannot.
        del client.headers["Origin"]
        assert client.get("/v2/agent/capabilities").status_code == 200
        assert client.delete(f"/v2/agent/conversations/{uuid4()}").status_code == 403
        duplicate = [("Cookie", "spb-agent-local=x"), ("Cookie", "spb-agent-local=y")]
        assert client.get("/v2/agent/capabilities", headers=duplicate).status_code == 401


def test_secure_cookie_default_and_disabled_route(tmp_path):
    with TestClient(app_at(tmp_path / "secure.db", [], browser=browser_config(secure=True, public_origin="https://agent.example")), base_url="https://agent.example") as client:
        response = client.post(SESSION_PATH, json={}, headers={"Authorization": f"Bearer {API_KEY}", "Origin": "https://agent.example"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("__Host-spb-agent=") and "Secure" in cookie and "Path=/" in cookie
    with TestClient(legacy_app(tmp_path / "legacy.db", []), headers=headers()) as client:
        assert client.post(SESSION_PATH, json={}).status_code == 404


def test_identity_reset_does_not_bypass_gateway_rate_limit(tmp_path):
    app = app_at(tmp_path / "agent.db", [])
    operations = app.user_middleware[0]
    operations.kwargs["config"] = replace(operations.kwargs["config"], rate_limit_requests=2)
    with TestClient(app, base_url=ORIGIN, headers=headers()) as client:
        first = client.post(SESSION_PATH, json={})
        second = client.post(SESSION_PATH, json={"reset": True})
        assert first.json()["session_ref"] != second.json()["session_ref"]
        limited = client.post(SESSION_PATH, json={"reset": True})
        assert limited.status_code == 429
        assert "set-cookie" not in limited.headers and "retry-after" in limited.headers


def test_public_browser_schema_matches_runtime(tmp_path):
    from pathlib import Path

    documented = json.loads((Path(__file__).resolve().parents[3] / "docs/openapi/assistant-agent-v2.openapi.json").read_text())
    runtime = app_at(tmp_path / "agent.db", []).openapi()
    for name in ("BrowserSessionRequest", "BrowserSessionResponse"):
        actual = runtime["components"]["schemas"][name]
        expected = documented["components"]["schemas"][name]
        assert actual["additionalProperties"] == expected["additionalProperties"] is False
        assert actual.get("required", []) == expected.get("required", [])
        assert set(actual["properties"]) == set(expected["properties"])
        for field, schema in actual["properties"].items():
            assert {key: value for key, value in schema.items() if key != "title"} == expected["properties"][field]
