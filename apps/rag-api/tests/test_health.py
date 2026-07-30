from __future__ import annotations

from fastapi.testclient import TestClient

from spb_rag_api.api.app import create_app
from spb_rag_api.settings import ApiSettings


def test_live_reports_workspace_contract() -> None:
    with TestClient(
        create_app(settings=ApiSettings(milvus_uri=""))
    ) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["phase"] == 5
    assert payload["checks"]["workspace"] == "ok"
    assert "spb_policy_chunks" in payload["checks"]["collection_contract"]


def test_ready_fails_when_milvus_is_not_configured() -> None:
    with TestClient(
        create_app(settings=ApiSettings(milvus_uri=""))
    ) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["reason"] == "milvus_not_configured"
