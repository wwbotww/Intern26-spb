import hashlib
import runpy
import subprocess
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from spb_assistant_api.api.app import create_app
from spb_assistant_api.deployed_app import DeploymentSettings
from spb_assistant_api.deployment_demo import PROXY_KEY, SIGNING_KEY, create_deployment_demo
from spb_assistant_api.storage_paths import init_store

ORIGIN = "https://localhost:13443"
ROOT = Path(__file__).resolve().parents[3]


def deployment_settings(tmp_path, **changes):
    return DeploymentSettings(**{
        "agent_enabled": True, "agent_managed_storage_enabled": True,
        "agent_database_path": str(tmp_path / "store/agent.db"),
        "agent_browser_session_enabled": True, "agent_browser_public_origin": ORIGIN,
        "api_keys": PROXY_KEY, "agent_browser_proxy_api_key": PROXY_KEY,
        "agent_browser_signing_key": SIGNING_KEY, **changes,
    })


@pytest.mark.parametrize("changes", [
    {"agent_enabled": False}, {"agent_managed_storage_enabled": False},
    {"agent_browser_session_enabled": False}, {"agent_browser_cookie_secure": False},
    {"auth_enabled": False}, {"api_keys": ""},
    {"agent_browser_public_origin": "http://localhost:13443"},
    {"agent_browser_proxy_api_key": "wrong-role"},
    {"deployment_ui_mode": "legacy"}, {"deployment_ui_mode": "unrecognised"},
])
def test_deployed_mode_rejects_inconsistent_flags_before_opening_database(tmp_path, changes):
    with pytest.raises(ValidationError):
        deployment_settings(tmp_path, **changes)
    assert not (tmp_path / "store").exists()


def test_controlled_entry_never_falls_back_to_synthetic_tools(tmp_path):
    init_store(tmp_path / "store")
    settings = deployment_settings(tmp_path)
    with TestClient(create_app(settings=settings), base_url=ORIGIN, headers={"Authorization": "Bearer " + PROXY_KEY, "Origin": ORIGIN}) as client:
        # An empty deployment must NOT pass readiness by inventing Fake capabilities.
        assert client.get("/v2/agent/health/ready").status_code == 503
        session = client.post("/v2/agent/browser-session", json={}).json()
        client.headers["X-Agent-Session"] = session["session_ref"]
        capabilities = client.get("/v2/agent/capabilities").json()
        assert len(capabilities) == 5 and not any(item["available"] for item in capabilities)


def test_synthetic_deployment_ignores_ambient_configuration_and_v1_rollback_preserves_state(tmp_path, monkeypatch):
    for field in ("QUERY_MODEL_ENABLED", "TRACKING_ENABLED", "OTEL_ENABLED"):
        monkeypatch.setenv("ASSISTANT_" + field, "true")
    monkeypatch.setenv("ASSISTANT_QUERY_MODEL_API_KEY", "sensitive-env-canary")

    async def forbidden_send(*args, **kwargs):
        raise AssertionError("No outbound HTTP in synthetic deployment")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_send)
    database = init_store(tmp_path / "store") / "agent.db"
    cookie_jar = None
    conversation = None
    for index in range(2):
        app = create_deployment_demo(database_path=database, public_origin=ORIGIN)
        with TestClient(app, base_url=ORIGIN, headers={"Authorization": "Bearer " + PROXY_KEY, "Origin": ORIGIN}, cookies=cookie_jar) as client:
            identity = client.post("/v2/agent/browser-session", json={})
            client.headers["X-Agent-Session"] = identity.json()["session_ref"]
            payload = {"message": "查邮件轨迹 1234567890123"}
            result = client.post("/v2/agent/messages", json=payload, headers={"Idempotency-Key": "same-creation"})
            assert result.status_code == 200 and result.json()["phase"] == "completed"
            if index == 0:
                assert "Secure" in identity.headers["set-cookie"] and "__Host-spb-agent" in identity.headers["set-cookie"]
                conversation = result.json()["conversation_id"]
                assert len(app.state.synthetic_tracking.commands) == 1
            else:
                assert result.json()["conversation_id"] == conversation
                assert app.state.synthetic_tracking.commands == []
            cookie_jar = client.cookies.jar
    original = hashlib.sha256(database.read_bytes()).hexdigest()
    legacy = create_deployment_demo(database_path=database, public_origin=ORIGIN, ui_mode="legacy")
    with TestClient(legacy, headers={"Authorization": "Bearer " + PROXY_KEY}) as client:
        assert client.get("/v2/agent/capabilities").status_code == 404
        result = client.post("/v1/chat", json={"mode": "policy", "question": "部署演练", "stream": False})
        assert result.status_code == 200 and "合成演练" in result.text
    assert hashlib.sha256(database.read_bytes()).hexdigest() == original


@pytest.mark.parametrize("origin", ["http://localhost:13443", "https://example.com", "https://192.168.1.1"])
def test_synthetic_demo_cannot_bind_public_business_origin(tmp_path, origin):
    with pytest.raises(ValueError):
        create_deployment_demo(database_path=tmp_path / "agent.db", public_origin=origin)


def test_compose_contract_keeps_init_runtime_public_and_operations_separate():
    compose = yaml.safe_load((ROOT / "deploy/agent/docker-compose.yml").read_text())
    services = compose["services"]
    api, web = services["assistant-api"], services["chat-web"]
    assert "ports" not in api and "env_file" not in api
    assert compose["networks"]["agent-private"]["internal"] is True
    assert api["networks"] == ["agent-private"]
    assert web["networks"] == ["agent-private", "agent-ingress"]
    assert api["depends_on"]["state-init"]["condition"] == "service_completed_successfully"
    assert web["depends_on"]["assistant-api"]["condition"] == "service_healthy"
    assert api["platform"] == "linux/amd64"
    assert services["storage"]["profiles"] == ["operations"]
    assert api["environment"]["ASSISTANT_AGENT_BROWSER_PROXY_API_KEY"] == web["environment"]["AGENT_PROXY_API_KEY"]
    assert api["environment"]["ASSISTANT_AGENT_BROWSER_PUBLIC_ORIGIN"] == web["environment"]["AGENT_PUBLIC_ORIGIN"]
    for name in ("state-init", "assistant-api", "chat-web", "storage"):
        assert services[name]["read_only"] is True
        assert services[name]["user"] == "10001:10001"
        assert services[name]["cap_drop"] == ["ALL"]


def test_nginx_and_image_contracts_keep_authentication_out_of_browser_assets():
    root = ROOT / "deploy/agent"
    config = (root / "nginx/nginx.conf.template").read_text()
    proxy = (root / "nginx/proxy.conf.template").read_text()
    assert 'location /api { return 404; }' in config
    assert "return 421" in config and "ssl_protocols TLSv1.2 TLSv1.3" in config
    assert "proxy_next_upstream off" in proxy and "proxy_buffering off" in proxy
    for name in ("X-API-Key", "X-Agent-Owner", "X-Forwarded-Host", "Forwarded"):
        assert f'proxy_set_header {name} "";' in proxy
    for image in ("Dockerfile.api", "Dockerfile.web"):
        assert "@sha256:" in (root / image).read_text()
    assert "AGENT_PROXY_API_KEY" not in (root / "Dockerfile.web").read_text()
    assert "synthetic" not in (root / "docker-compose.yml").read_text()


def test_actions_are_read_only_pinned_offline_and_do_not_deploy_on_pull_request():
    workflow = yaml.safe_load((ROOT / ".github/workflows/agent-ci.yml").read_text())
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in workflow.get("on", workflow.get(True, {}))
    for job in workflow["jobs"].values():
        assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] <= 40
        for step in job["steps"]:
            if "uses" in step:
                assert len(step["uses"].split("@")[1]) == 40
    raw = (ROOT / ".github/workflows/agent-ci.yml").read_text()
    assert "secrets." not in raw and "continue-on-error" not in raw
    assert "--all-packages" in raw and "ci_offline_eval" in raw and "smoke.py" in raw


@pytest.mark.parametrize("endpoint", ["tcp://remote.invalid:2375", "ssh://remote.invalid"])
def test_synthetic_drill_rejects_remote_docker_before_creating_resources(monkeypatch, endpoint):
    drill = runpy.run_path(str(ROOT / "deploy/agent/smoke.py"))["Drill"]
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    with pytest.raises(ValueError, match="remote Docker host"):
        drill()


def test_synthetic_drill_pins_local_endpoint_despite_ambient_context(tmp_path, monkeypatch):
    namespace = runpy.run_path(str(ROOT / "deploy/agent/smoke.py"))
    endpoint = "unix:///synthetic-docker.sock"
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-context-canary")
    monkeypatch.setattr(namespace["tempfile"], "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(namespace["ssl"], "create_default_context", lambda **kwargs: None)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "openssl":
            for option in ("-keyout", "-out"):
                Path(command[command.index(option) + 1]).touch()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(namespace["subprocess"], "run", fake_run)
    drill = namespace["Drill"](port=13443)
    drill.compose("config", "--quiet")
    command, options = calls[-1]
    assert command[:4] == ["docker", "--host", endpoint, "compose"]
    assert "DOCKER_CONTEXT" not in options["env"]
    assert command[command.index("--env-file") + 1] == "/dev/null"
