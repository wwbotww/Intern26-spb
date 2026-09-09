from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
MONITORING = ROOT / "deploy" / "observability"


def _yaml(path: str):
    return yaml.safe_load((MONITORING / path).read_text(encoding="utf-8"))


def test_monitoring_compose_is_isolated_loopback_and_unpaid():
    compose = _yaml("docker-compose.yml")
    assert compose["name"] == "intern26-agent-observability"
    assert set(compose["services"]) == {
        "agent-demo",
        "prometheus",
        "tempo",
        "grafana",
    }
    for service in compose["services"].values():
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "env_file" not in service
        assert all(port.startswith("127.0.0.1:") for port in service["ports"])
        assert ":latest" not in service["image"]
        assert all(
            mount.endswith(":ro") for mount in service.get("volumes", [])
        )
    demo = compose["services"]["agent-demo"]
    assert demo["environment"]["ASSISTANT_QUERY_MODEL_ENABLED"] == "false"
    assert demo["environment"]["ASSISTANT_OTEL_SAMPLE_RATIO"] == "1"
    assert demo["command"] == ["/app/.venv/bin/spb-assistant-agent-demo"]
    assert "_API_KEY" not in json.dumps(compose)
    assert (
        "agent-demo"
        not in yaml.safe_load(
            (ROOT / "deploy/docker-compose.yml").read_text()
        )["services"]
    )


def test_dashboard_datasource_uids_and_metric_names_match_runtime():
    sources = _yaml("grafana/provisioning/datasources/agent.yml")[
        "datasources"
    ]
    assert {source["uid"] for source in sources} == {
        "agent-prometheus",
        "agent-tempo",
    }
    dashboard = json.loads(
        (MONITORING / "grafana/dashboards/agent-workflow.json").read_text()
    )
    assert dashboard["uid"] == "agent-workflow"
    panels = dashboard["panels"]
    assert len({panel["id"] for panel in panels}) == len(panels)
    for panel in panels:
        if panel["type"] == "text":
            continue
        assert panel["datasource"]["uid"] in {
            source["uid"] for source in sources
        }
    from spb_assistant_api.observability.metrics import ServiceMetrics

    metrics = ServiceMetrics().render().decode()
    for name in (
        "assistant_agent_workflow_invocations",
        "assistant_agent_node_executions",
        "assistant_agent_node_duration_seconds",
        "assistant_agent_trace_export_batches",
        "assistant_agent_telemetry_errors",
    ):
        assert name in metrics and name in json.dumps(dashboard)
    assert "holdout" in panels[0]["options"]["content"]
    link = panels[-1]["fieldConfig"]["overrides"][0]["properties"][0]["value"][
        0
    ]["url"]
    assert link.startswith("/d/agent-trace?var-trace_id=")
    detail = json.loads(
        (MONITORING / "grafana/dashboards/agent-trace.json").read_text()
    )
    assert detail["uid"] == "agent-trace"
    assert detail["panels"][1]["type"] == "traces"
    grafana = _yaml("docker-compose.yml")["services"]["grafana"]["environment"]
    assert grafana["GF_AUTH_ANONYMOUS_ORG_ROLE"] == "Viewer"
    assert grafana["GF_SECURITY_DISABLE_INITIAL_ADMIN_CREATION"] == "true"
