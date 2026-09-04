from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from spb_assistant_api.agent_demo import DEMO_MAIL_NO, create_demo_app


def test_demo_composition_owns_agent_lifespan_and_exposes_sse(
    tmp_path: Path,
) -> None:
    app = create_demo_app(database_path=tmp_path / "agent-demo.db")

    assert app.state.agent_api is None
    with TestClient(app) as client:
        assert app.state.agent_api is not None
        capabilities = client.get("/v2/agent/capabilities")
        response = client.post(
            "/v2/agent/messages",
            headers={
                "Idempotency-Key": "demo-message",
                "X-Request-ID": "demo-request",
            },
            json={
                "message": f"查询邮件 {DEMO_MAIL_NO}",
                "explicit_intent": "tracking",
                "stream": True,
            },
        )

    assert app.state.agent_api is None
    assert capabilities.status_code == 200
    available = {
        item["intent"]
        for item in capabilities.json()
        if item["available"]
    }
    assert available == {
        "policy",
        "device_price",
        "tracking",
        "delivery_time",
        "postage",
    }
    assert response.status_code == 200
    assert "event: done" in response.text
    done_block = next(
        block
        for block in response.text.split("\n\n")
        if block.startswith("event: done")
    )
    payload = json.loads(done_block.split("data: ", 1)[1])
    assert payload["response"]["phase"] == "completed"
    assert payload["response"]["result"]["type"] == "tracking"
