"""Synthetic, local-only checks for this directory's disposable Compose demo.

Run only after starting deploy/observability/docker-compose.yml. No dotenv,
credentials, Assistant internals, production endpoints or model requests.
"""

from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx

API = "http://127.0.0.1:18081"
PROMETHEUS = "http://127.0.0.1:19091"
TEMPO = "http://127.0.0.1:13200"
GRAFANA = "http://127.0.0.1:13001"
TRACE_QUERY = '{ resource.service.name = "spb-assistant-agent" && name = "agent.workflow" }'


def _eventually(check):
    deadline = time.monotonic() + 30
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise RuntimeError("local_telemetry_check_timed_out")
        time.sleep(0.5)


def main() -> None:
    with httpx.Client(timeout=10, trust_env=False) as client:
        for url in (
            API + "/v2/agent/health/ready",
            TEMPO + "/ready",
            GRAFANA + "/api/health",
        ):
            client.get(url).raise_for_status()

        def search():
            response = client.get(
                TEMPO + "/api/search", params={"q": TRACE_QUERY, "limit": 100}
            )
            response.raise_for_status()
            return {
                trace["traceID"] for trace in response.json().get("traces", [])
            }

        prior = search()
        prefix = f"phase5e-smoke-{uuid4().hex}"
        logical = 0
        replays = 0

        def send(index, message, *, conversation=None, replay=False):
            nonlocal logical, replays
            payload = {"message": message, "stream": False}
            if conversation:
                payload["conversation_id"] = conversation
            response = client.post(
                API + "/v2/agent/messages",
                json=payload,
                headers={"Idempotency-Key": f"{prefix}-{index}"},
            )
            response.raise_for_status()
            if replay:
                replays += 1
            else:
                logical += 1
            return response.json()

        tracking = send(1, "查邮件 1234567890123")
        assert tracking["phase"] == "completed"
        assert (
            send(1, "查邮件 1234567890123", replay=True)["turn_id"]
            == tracking["turn_id"]
        )
        postage = send(2, "查邮费")
        assert postage["phase"] == "waiting_user"
        conversation = postage["conversation_id"]
        assert (
            send(3, "北京寄上海", conversation=conversation)["phase"]
            == "waiting_user"
        )
        completed = send(4, "2公斤", conversation=conversation)
        assert completed["phase"] == "completed"
        assert (
            send(4, "2公斤", conversation=conversation, replay=True)["turn_id"]
            == completed["turn_id"]
        )
        assert send(5, "早上好")["phase"] == "handoff"

        def new_traces():
            current = search() - prior
            return current if len(current) >= logical else None

        trace_ids = _eventually(new_traces)
        assert len(trace_ids) == logical
        node_count = 0
        for trace_id in sorted(trace_ids):
            response = client.get(
                TEMPO + "/api/traces/" + trace_id,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
            encoded = json.dumps(payload, ensure_ascii=False)
            assert (
                "1234567890123" not in encoded and "北京寄上海" not in encoded
            )
            assert (
                "exception.message" not in encoded
                and "latest_message" not in encoded
            )
            spans = [
                span
                for batch in payload.get(
                    "batches", payload.get("resourceSpans", [])
                )
                for scope in batch.get("scopeSpans", [])
                for span in scope.get("spans", [])
            ]
            (root,) = [
                span for span in spans if span["name"] == "agent.workflow"
            ]
            nodes = [
                span
                for span in spans
                if span["name"].startswith("agent.node.")
            ]
            assert nodes and all(
                span["parentSpanId"] == root["spanId"] for span in nodes
            )
            node_count += len(nodes)

        def scraped():
            response = client.get(
                PROMETHEUS + "/api/v1/query",
                params={
                    "query": 'sum(assistant_agent_workflow_invocations_total{job="spb-assistant-agent"})'
                },
            )
            response.raise_for_status()
            rows = response.json()["data"]["result"]
            return rows and float(rows[0]["value"][1]) >= logical

        _eventually(scraped)
        dashboard = client.get(GRAFANA + "/api/dashboards/uid/agent-workflow")
        dashboard.raise_for_status()
        panels = dashboard.json()["dashboard"]["panels"]
        # Grafana provisioning + each PromQL / TraceQL query must be executable.
        for panel in panels:
            for target in panel.get("targets", []):
                if "expr" in target:
                    response = client.get(
                        PROMETHEUS + "/api/v1/query",
                        params={
                            "query": target["expr"].replace(
                                "$__rate_interval", "1m"
                            )
                        },
                    )
                    response.raise_for_status()
                    assert response.json()["status"] == "success"
        print(
            json.dumps(
                {
                    "logical_invocations": logical,
                    "http_replays": replays,
                    "new_workflow_traces": len(trace_ids),
                    "node_spans": node_count,
                    "dashboard_panels": len(panels),
                    "paid_model_requests": 0,
                    "scope": "isolated-local-compose-synthetic",
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
