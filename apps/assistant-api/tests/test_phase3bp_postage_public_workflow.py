import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from spb_eval.schemas import AgentPublicResponse

from .postage_p3_fixture import API_KEY, OTHER_KEY, QUERY, CONFIRM, app_at, p3_config


def send(client, message, *, conversation=None, key=None, owner=API_KEY, **kwargs):
    payload = {"message": message, **kwargs}
    if conversation:
        payload["conversation_id"] = conversation
    return client.post("/v2/agent/messages", json=payload, headers={
        "Authorization": f"Bearer {owner}", "Idempotency-Key": key or str(uuid4()),
    })


def test_product_interrupt_confirm_restart_and_replay(tmp_path):
    transports = []
    app = app_at(tmp_path / "agent.db", transports)
    with TestClient(app) as client:
        first = send(client, "从北京寄到上海，1.25公斤，查邮费").json()
        assert first["phase"] == "waiting_user"
        assert first["required_inputs"][0]["choices"] == ["SYN-A", "SYN-B"]
        conversation = first["conversation_id"]
        review = send(client, "询价产品：SYN-A", conversation=conversation).json()
        assert review["required_inputs"][0]["name"] == "postage_confirmation"
        assert "1250 克" in review["reply"]
        assert transports[0].calls == []
    assert transports[0].closed
    with TestClient(app) as client:
        completed = send(client, CONFIRM, conversation=conversation, key="approve")
        assert completed.status_code == 200, completed.text
        result = completed.json()
        assert result["phase"] == "completed", result
        assert result["result"]["data"]["amount"] == "12.30"
        assert result["result"]["quote_basis"]["source"]["source_type"] == "fake_gateway"
        assert len(transports[1].calls) == 1
        replay = send(client, CONFIRM, conversation=conversation, key="approve").json()
        assert replay["result"] == result["result"]
        assert len(transports[1].calls) == 1
        fresh = send(client, QUERY, conversation=conversation).json()
        assert fresh["phase"] == "waiting_user"
        assert send(client, CONFIRM, conversation=conversation).json()["phase"] == "completed"
        assert len(transports[1].calls) == 2
    assert all(transport.closed for transport in transports)


@pytest.mark.parametrize("change,label,amount", [("重量：2公斤", "2000 克", "16.00"), ("询价产品：SYN-B", "SYN-B", "18.40")])
def test_edited_conditions_need_new_review_not_just_overwrite(tmp_path, change, label, amount):
    transports = []
    with TestClient(app_at(tmp_path / "agent.db", transports)) as client:
        review = send(client, QUERY).json()
        conversation = review["conversation_id"]
        conflict = send(client, change, conversation=conversation).json()
        assert "confirm_overwrite=true" in conflict["required_inputs"][0]["validation_hint"]
        edited = send(client, change, conversation=conversation, confirm_overwrite=True).json()
        assert edited["required_inputs"][0]["name"] == "postage_confirmation"
        assert label in edited["reply"]
        assert transports[0].calls == []
        final = send(client, CONFIRM, conversation=conversation).json()
        assert final["result"]["data"]["amount"] == amount


@pytest.mark.parametrize("text", [QUERY + "，确认基础询价", QUERY])
def test_cannot_preapprove_initial_query(tmp_path, text):
    transports = []
    with TestClient(app_at(tmp_path / "agent.db", transports)) as client:
        first = send(client, text).json()
        assert first["phase"] == "waiting_user"
        wrong = send(client, "确认基础询价，同时重量改为2公斤", conversation=first["conversation_id"], confirm_overwrite=True).json()
        assert wrong["phase"] == "waiting_user"
        assert "2000 克" in wrong["reply"]
        assert transports[0].calls == []


def test_pricing_scope_change_invalidates_suspended_review(tmp_path):
    transports = []
    with TestClient(app_at(tmp_path / "agent.db", transports)) as client:
        first = send(client, QUERY).json()
    cfg = p3_config()
    cfg = cfg.model_copy(update={"profile": cfg.profile.model_copy(update={"pricing_scope_ref": "synthetic-new-principal"})})
    with TestClient(app_at(tmp_path / "agent.db", transports, cfg=cfg)) as client:
        result = send(client, CONFIRM, conversation=first["conversation_id"]).json()
        assert result["failure"]["code"] == "postage_context_changed_restart"
        assert transports[-1].calls == []


def test_offline_auth_owner_capabilities_readiness_and_delete(tmp_path, monkeypatch):
    # Host settings must not accidentally enable the model, tracking or exporter.
    for key in ("QUERY_MODEL_ENABLED", "TRACKING_ENABLED", "OTEL_ENABLED"):
        monkeypatch.setenv("ASSISTANT_" + key, "true")
    monkeypatch.setenv("ASSISTANT_AUTH_ENABLED", "false")
    transports = []
    with TestClient(app_at(tmp_path / "agent.db", transports)) as client:
        assert client.get("/v2/agent/capabilities").status_code == 401
        headers = {"Authorization": f"Bearer {API_KEY}"}
        caps = client.get("/v2/agent/capabilities", headers=headers).json()
        postage = next(c for c in caps if c["intent"] == "postage")
        assert postage["available"] and postage["capability_version"] == "phase-3b-p3"
        assert "product_code" in [item["name"] for item in postage["required_inputs"]]
        assert all(not c["available"] for c in caps if c["intent"] != "postage")
        ready = client.get("/v2/agent/health/ready", headers=headers)
        assert ready.status_code == 200
        assert ready.json()["checks"]["capability.postage"] == "ready"
        first = send(client, QUERY).json()
        conversation = first["conversation_id"]
        assert send(client, CONFIRM, conversation=conversation, owner=OTHER_KEY).status_code == 404
        assert client.delete(f"/v2/agent/conversations/{conversation}", headers=headers).status_code == 204
        assert send(client, CONFIRM, conversation=conversation).status_code == 404
        assert transports[0].calls == []


def test_json_and_sse_projection_replay_are_equivalent(tmp_path):
    transports = []
    with TestClient(app_at(tmp_path / "agent.db", transports)) as client:
        first = send(client, QUERY).json()
        response = send(client, CONFIRM, conversation=first["conversation_id"], key="same-turn", stream=True)
        assert response.status_code == 200
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        done = frames[-1]["response"]
        assert done["phase"] == "completed", done
        replay = send(client, CONFIRM, conversation=first["conversation_id"], key="same-turn").json()
        assert replay["result"] == done["result"]
        assert len(transports[0].calls) == 1
        assert "SYNTHETIC-PRIVATE-MESSAGE" not in response.text
        assert "policy_fingerprint" not in response.text
        AgentPublicResponse.model_validate(replay)


@pytest.mark.parametrize("scenario,category", [("business-refusal", "upstream_unavailable"), ("malformed", "contract_violation"), ("timeout", "upstream_timeout")])
def test_failure_is_public_safe_and_never_a_fake_zero_quote(tmp_path, scenario, category):
    transports = []
    with TestClient(app_at(tmp_path / "agent.db", transports, scenario=scenario)) as client:
        first = send(client, QUERY).json()
        response = send(client, CONFIRM, conversation=first["conversation_id"])
        final = response.json()
        assert final["phase"] == "failed", final
        assert final["result"] is None
        assert final["failure"]["category"] == category
        assert "SYNTHETIC-PRIVATE-MESSAGE" not in response.text
        AgentPublicResponse.model_validate(final)
        assert len(transports[0].calls) == (2 if scenario == "timeout" else 1)


@pytest.mark.parametrize("text,code", [(QUERY + "，保价500元", "postage_scope_not_supported"), (QUERY.replace("1.25", "0.0001"), "postage_weight_not_supported"), (QUERY + "，不需要保价", "postage_scope_not_supported")])
def test_scope_is_fail_closed_with_documented_negation_limitation(tmp_path, text, code):
    transports = []
    with TestClient(app_at(tmp_path / "agent.db", transports)) as client:
        result = send(client, text, explicit_intent="postage").json()
        assert result["failure"]["code"] == code
        assert transports[0].calls == []


def test_p3_refuses_missing_identity_unprotected_or_implicit_database(tmp_path):
    import httpx
    from spb_assistant_api.offline_postage import create_offline_postage_app
    from .postage_p1_fixture import preflight
    from .postage_p2_fixture import config
    values = dict(database_path=tmp_path / "agent.db", api_keys=API_KEY, config=p3_config(), catalog=preflight().catalog, transport_factory=lambda: httpx.MockTransport(lambda request: httpx.Response(500)))
    for changes in ({"config": config()}, {"api_keys": ""}, {"database_path": tmp_path.__class__("relative.db")}):
        with pytest.raises(ValueError):
            create_offline_postage_app(**{**values, **changes})
    app = create_offline_postage_app(**{**values, "transport_factory": lambda: httpx.AsyncHTTPTransport()})
    with pytest.raises(ValueError, match="MockTransport"):
        with TestClient(app):
            pass
