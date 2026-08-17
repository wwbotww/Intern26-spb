from __future__ import annotations

from fastapi.testclient import TestClient

from spb_assistant_api.api.app import create_app
from spb_assistant_api.domain.models import (
    DevicePriceEvidence,
    PolicyEvidence,
    QueryMode,
    ToolResult,
    ToolStatus,
)
from spb_assistant_api.services.dispatcher import (
    DEVICE_PRICE_TOOL_NAME,
    POLICY_TOOL_NAME,
)
from spb_assistant_api.settings import AssistantSettings

from .fakes import FakeTool


def _tools() -> dict[QueryMode, FakeTool]:
    return {
        QueryMode.POLICY: FakeTool(
            name=POLICY_TOOL_NAME,
            result=ToolResult(
                tool=POLICY_TOOL_NAME,
                status=ToolStatus.SUCCESS,
                answer="政策回答",
                evidence=(
                    PolicyEvidence(
                        evidence_id="policy-1",
                        title="政策",
                        source_url="https://example.test/policy",
                        excerpt="依据",
                    ),
                ),
            ),
        ),
        QueryMode.DEVICE_PRICE: FakeTool(
            name=DEVICE_PRICE_TOOL_NAME,
            result=ToolResult(
                tool=DEVICE_PRICE_TOOL_NAME,
                status=ToolStatus.SUCCESS,
                answer="价格回答",
                evidence=(
                    DevicePriceEvidence(
                        evidence_id="price-1",
                        title="设备",
                        brand="品牌",
                        model="型号",
                        specification="规格",
                        price="1.00",
                        currency="CNY",
                        source="来源",
                        observed_at="2026-08-01T00:00:00Z",
                    ),
                ),
            ),
        ),
    }


def _payload() -> dict[str, object]:
    return {
        "mode": "policy",
        "question": "政策问题",
        "stream": False,
    }


def test_auth_accepts_bearer_and_rejects_missing_key() -> None:
    app = create_app(
        settings=AssistantSettings(
            auth_enabled=True,
            api_keys="test-key",
            rate_limit_enabled=False,
        ),
        tools=_tools(),
    )

    with TestClient(app) as client:
        missing = client.post("/v1/chat", json=_payload())
        accepted = client.post(
            "/v1/chat",
            headers={"Authorization": "Bearer test-key"},
            json=_payload(),
        )

    assert missing.status_code == 401
    assert accepted.status_code == 200


def test_rate_limit_and_request_id_are_applied() -> None:
    app = create_app(
        settings=AssistantSettings(
            auth_enabled=False,
            rate_limit_enabled=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=60,
        ),
        tools=_tools(),
    )

    with TestClient(app) as client:
        first = client.post(
            "/v1/chat",
            headers={"X-Request-ID": "known-request"},
            json=_payload(),
        )
        rejected = client.post("/v1/chat", json=_payload())

    assert first.status_code == 200
    assert first.headers["x-request-id"] == "known-request"
    assert rejected.status_code == 429
    assert rejected.headers["x-ratelimit-remaining"] == "0"


def test_metrics_exposes_assistant_http_series() -> None:
    app = create_app(
        settings=AssistantSettings(
            auth_enabled=False,
            rate_limit_enabled=False,
        ),
        tools=_tools(),
    )

    with TestClient(app) as client:
        client.get("/health/live")
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "assistant_http_requests_total" in response.text
    assert "assistant_auth_failures_total" in response.text
