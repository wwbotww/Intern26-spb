from __future__ import annotations

from fastapi.testclient import TestClient

from spb_rag_api.api.app import create_app
from spb_rag_api.domain.models import SearchHit, SearchQuery
from spb_rag_api.settings import ApiSettings


class FakeRetriever:
    def __init__(self) -> None:
        self.query: SearchQuery | None = None

    async def initialize(self) -> None:
        return None

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        self.query = query
        return [
            SearchHit(
                chunk_id="chunk-1",
                document_id="document-1",
                title="邮政业标准化管理办法",
                text="第一条 为加强邮政业标准化工作……",
                source_url="https://www.spb.gov.cn/example.html",
                section_path="第一章/第一条",
                score=0.032,
                document_type="html",
                published_at="2024-01-01",
                document_no="国邮发〔2024〕1号",
                source_org="国家邮政局",
                validity_status="有效",
                chunk_index=0,
            )
        ]

    def readiness(self) -> dict[str, str]:
        return {
            "retriever": "ready",
            "embedding": "ready",
            "milvus": "ready",
        }

    async def close(self) -> None:
        return None


def test_retrieve_maps_request_and_results() -> None:
    retriever = FakeRetriever()
    settings = ApiSettings(
        auth_enabled=False,
        deepseek_api_key="",
        rerank_enabled=False,
        search_default_top_k=5,
        search_max_top_k=20,
        search_candidate_k=40,
    )
    app = create_app(settings=settings, retriever=retriever)

    with TestClient(app) as client:
        response = client.post(
            "/v1/retrieve",
            json={
                "query": "邮政业标准如何制定？",
                "top_k": 3,
                "candidate_k": 12,
                "filters": {
                    "validity_statuses": ["有效", "有效"],
                    "published_from": "2020-01-01",
                },
            },
        )
        ready = client.get("/health/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "hybrid_rrf"
    assert payload["count"] == 1
    assert payload["results"][0]["rank"] == 1
    assert payload["results"][0]["document_no"] == "国邮发〔2024〕1号"
    assert retriever.query is not None
    assert retriever.query.top_k == 3
    assert retriever.query.candidate_k == 12
    assert retriever.query.filters.validity_statuses == ("有效",)
    assert retriever.query.filters.published_from == "2020-01-01"
    assert ready.status_code == 503
    assert ready.json()["checks"]["deepseek"] == "not_ready"


def test_retrieve_rejects_service_limit_violation() -> None:
    app = create_app(
        settings=ApiSettings(
            auth_enabled=False,
            search_max_top_k=10,
        ),
        retriever=FakeRetriever(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/retrieve",
            json={"query": "快递暂行条例", "top_k": 11},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_search_request"


def test_retrieve_rejects_excessive_candidate_count() -> None:
    app = create_app(
        settings=ApiSettings(
            auth_enabled=False,
            search_max_candidate_k=50,
        ),
        retriever=FakeRetriever(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/retrieve",
            json={
                "query": "快递暂行条例",
                "top_k": 5,
                "candidate_k": 51,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_search_request"


def test_retrieve_is_unavailable_without_configuration() -> None:
    with TestClient(
        create_app(
            settings=ApiSettings(
                auth_enabled=False,
                milvus_uri="",
            )
        )
    ) as client:
        response = client.post(
            "/v1/retrieve",
            json={"query": "快递暂行条例"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "milvus_not_configured"
