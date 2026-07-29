from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from spb_rag_api.api.app import create_app
from spb_rag_api.domain.models import ChatEvent, SearchHit, SearchQuery
from spb_rag_api.settings import ApiSettings


class FakeRetriever:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.query: SearchQuery | None = None

    async def initialize(self) -> None:
        return None

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        self.query = query
        return self.hits

    def readiness(self) -> dict[str, str]:
        return {
            "retriever": "ready",
            "embedding": "ready",
            "milvus": "ready",
        }

    async def close(self) -> None:
        return None


class FakeChatProvider:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] | None = None
        self.calls = 0

    @property
    def model(self) -> str:
        return "deepseek-v4-flash"

    async def stream(
        self,
        *,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[ChatEvent]:
        self.calls += 1
        self.messages = messages
        yield ChatEvent(event="delta", data={"content": "依据[1]，"})
        yield ChatEvent(event="delta", data={"content": "应依法办理。"})
        yield ChatEvent(
            event="usage",
            data={
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
            },
        )
        yield ChatEvent(
            event="done",
            data={"finish_reason": "stop"},
        )

    def readiness(self) -> dict[str, str]:
        return {"deepseek": "ready"}

    async def close(self) -> None:
        return None


def _hit() -> SearchHit:
    return SearchHit(
        chunk_id="chunk-1",
        document_id="document-1",
        title="快递暂行条例",
        text="经营快递业务，应当依法取得快递业务经营许可。",
        source_url="https://www.spb.gov.cn/example.html",
        section_path="第二章",
        score=0.032,
        document_type="html",
        published_at="2018-03-27",
        document_no="国务院令第697号",
        source_org="国务院",
        validity_status="有效",
        chunk_index=2,
    )


def _app(
    retriever: FakeRetriever,
    provider: FakeChatProvider | None,
):
    return create_app(
        settings=ApiSettings(
            auth_enabled=False,
            milvus_uri="",
            deepseek_api_key="test-key",
        ),
        retriever=retriever,
        chat_provider=provider,
    )


def test_streaming_chat_returns_metadata_deltas_usage_and_done() -> None:
    retriever = FakeRetriever([_hit()])
    provider = FakeChatProvider()

    with TestClient(_app(retriever, provider)) as client:
        response = client.post(
            "/v1/chat",
            json={
                "question": "经营快递业务需要什么许可？",
                "stream": True,
                "top_k": 3,
            },
        )
        ready = client.get("/health/ready")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "text/event-stream"
    )
    body = response.text
    assert "event: metadata" in body
    assert "event: delta" in body
    assert "event: usage" in body
    assert "event: done" in body
    assert "国务院令第697号" in body
    assert provider.messages is not None
    assert "<knowledge_base>" in provider.messages[1]["content"]
    assert retriever.query is not None
    assert retriever.query.top_k == 3
    assert ready.status_code == 200


def test_non_streaming_chat_collects_answer_and_citations() -> None:
    provider = FakeChatProvider()

    with TestClient(
        _app(FakeRetriever([_hit()]), provider)
    ) as client:
        response = client.post(
            "/v1/chat",
            json={
                "question": "经营快递业务需要什么许可？",
                "stream": False,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "依据[1]，应依法办理。"
    assert payload["citations"][0]["source_url"].startswith("https://")
    assert payload["usage"]["total_tokens"] == 110
    assert payload["finish_reason"] == "stop"


def test_no_context_short_circuits_model_call() -> None:
    provider = FakeChatProvider()

    with TestClient(
        _app(FakeRetriever([]), provider)
    ) as client:
        response = client.post(
            "/v1/chat",
            json={"question": "不存在的问题", "stream": False},
        )

    assert response.status_code == 200
    assert "资料不足" in response.json()["answer"]
    assert response.json()["citations"] == []
    assert provider.calls == 0


def test_chat_requires_provider_configuration() -> None:
    app = create_app(
            settings=ApiSettings(
                auth_enabled=False,
                milvus_uri="",
                deepseek_api_key="",
        ),
        retriever=FakeRetriever([_hit()]),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={"question": "问题"},
        )

    assert response.status_code == 503
    assert (
        response.json()["detail"]["code"]
        == "chat_provider_unavailable"
    )
