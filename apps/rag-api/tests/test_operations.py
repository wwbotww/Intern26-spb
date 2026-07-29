from __future__ import annotations

from fastapi.testclient import TestClient

from spb_rag_api.api.app import create_app
from spb_rag_api.domain.models import SearchHit, SearchQuery
from spb_rag_api.settings import ApiSettings


class FakeRetriever:
    async def initialize(self) -> None:
        return None

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        return []

    def readiness(self) -> dict[str, str]:
        return {
            "retriever": "ready",
            "embedding": "ready",
            "milvus": "ready",
        }

    async def close(self) -> None:
        return None


def _settings(**overrides: object) -> ApiSettings:
    return ApiSettings(
        milvus_uri="",
        deepseek_api_key="",
        **overrides,
    )


def test_auth_accepts_bearer_and_x_api_key() -> None:
    app = create_app(
        settings=_settings(
            auth_enabled=True,
            api_keys="primary-key,rotating-key",
            rate_limit_enabled=False,
        ),
        retriever=FakeRetriever(),
    )

    with TestClient(app) as client:
        missing = client.post(
            "/v1/retrieve",
            json={"query": "问题"},
        )
        invalid = client.post(
            "/v1/retrieve",
            headers={"Authorization": "Bearer wrong"},
            json={"query": "问题"},
        )
        bearer = client.post(
            "/v1/retrieve",
            headers={"Authorization": "Bearer primary-key"},
            json={"query": "问题"},
        )
        header = client.post(
            "/v1/retrieve",
            headers={"X-API-Key": "rotating-key"},
            json={"query": "问题"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert invalid.headers["www-authenticate"] == "Bearer"
    assert bearer.status_code == 200
    assert header.status_code == 200


def test_auth_fails_closed_when_enabled_without_keys() -> None:
    app = create_app(
        settings=_settings(
            auth_enabled=True,
            api_keys="",
        ),
        retriever=FakeRetriever(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/retrieve",
            json={"query": "问题"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "auth_not_configured"


def test_rate_limit_and_request_id_headers() -> None:
    app = create_app(
        settings=_settings(
            auth_enabled=False,
            rate_limit_enabled=True,
            rate_limit_requests=2,
            rate_limit_window_seconds=60,
        ),
        retriever=FakeRetriever(),
    )

    with TestClient(app) as client:
        first = client.post(
            "/v1/retrieve",
            headers={"X-Request-ID": "known-request"},
            json={"query": "问题"},
        )
        second = client.post(
            "/v1/retrieve",
            json={"query": "问题"},
        )
        rejected = client.post(
            "/v1/retrieve",
            json={"query": "问题"},
        )

    assert first.status_code == 200
    assert first.headers["x-request-id"] == "known-request"
    assert len(second.headers["x-request-id"]) == 32
    assert rejected.status_code == 429
    assert rejected.headers["x-ratelimit-remaining"] == "0"
    assert int(rejected.headers["retry-after"]) >= 1


def test_oversized_request_is_rejected_before_json_parsing() -> None:
    app = create_app(
        settings=_settings(
            auth_enabled=False,
            rate_limit_enabled=False,
            max_request_body_bytes=1024,
        ),
        retriever=FakeRetriever(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/retrieve",
            content=b"x" * 2048,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_too_large"


def test_metrics_exposes_http_auth_and_rate_limit_series() -> None:
    app = create_app(
        settings=_settings(
            auth_enabled=False,
            rate_limit_enabled=False,
        ),
        retriever=FakeRetriever(),
    )

    with TestClient(app) as client:
        client.get("/health/live")
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "text/plain; version="
    )
    assert "spb_http_requests_total" in response.text
    assert "spb_auth_failures_total" in response.text
    assert "spb_rate_limit_rejections_total" in response.text
