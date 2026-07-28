from __future__ import annotations

from fastapi.testclient import TestClient

from spb_rag_api.api.app import create_app


def test_live_reports_workspace_contract():
    response = TestClient(create_app()).get("/health/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["phase"] == 1
    assert payload["checks"]["workspace"] == "ok"
    assert "spb_policy_chunks" in payload["checks"]["collection_contract"]
