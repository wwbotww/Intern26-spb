from __future__ import annotations

import asyncio
from typing import Any

from spb_contracts import DENSE_FIELD, REQUIRED_FIELDS
from spb_rag_api.adapters.milvus import (
    MilvusHybridSearchStore,
    MilvusReadConfig,
    build_filter_expression,
)
from spb_rag_api.domain.models import SearchFilters, SearchQuery


class FakeMilvusClient:
    def __init__(self) -> None:
        self.loaded = False
        self.search_kwargs: dict[str, Any] | None = None
        self.closed = False

    def has_collection(self, collection: str) -> bool:
        return collection == "spb_policy_chunks"

    def describe_collection(self, collection: str) -> dict[str, Any]:
        assert collection == "spb_policy_chunks"
        fields = [
            {
                "name": name,
                "params": {"dim": 768} if name == DENSE_FIELD else {},
            }
            for name in REQUIRED_FIELDS
        ]
        return {"fields": fields}

    def list_indexes(self, collection: str) -> list[str]:
        assert collection == "spb_policy_chunks"
        return ["text_dense_index", "text_sparse_index"]

    def describe_index(
        self,
        collection: str,
        *,
        index_name: str,
    ) -> dict[str, str]:
        assert collection == "spb_policy_chunks"
        return {
            "text_dense_index": {
                "field_name": "text_dense",
                "metric_type": "COSINE",
            },
            "text_sparse_index": {
                "field_name": "text_sparse",
                "metric_type": "BM25",
            },
        }[index_name]

    def load_collection(self, **kwargs: Any) -> None:
        assert kwargs["collection_name"] == "spb_policy_chunks"
        self.loaded = True

    def hybrid_search(self, **kwargs: Any) -> list[list[dict[str, Any]]]:
        self.search_kwargs = kwargs
        return [
            [
                {
                    "id": "chunk-1",
                    "distance": 0.031,
                    "entity": {
                        "document_id": "document-1",
                        "title": "快递暂行条例",
                        "text": "经营快递业务，应当依法取得许可。",
                        "source_url": "https://www.spb.gov.cn/example.html",
                        "document_type": "html",
                        "published_at": "2018-03-27",
                        "document_no": "国务院令第697号",
                        "source_org": "国务院",
                        "validity_status": "有效",
                        "section_path": "第二章",
                        "chunk_index": 2,
                    },
                }
            ]
        ]

    def close(self) -> None:
        self.closed = True


def test_filter_expression_only_uses_structured_fields() -> None:
    expression = build_filter_expression(
        SearchFilters(
            document_types=("html", "pdf"),
            validity_statuses=("有效",),
            source_orgs=('国家邮政局" or true',),
            published_from="2020-01-01",
            published_through="2025-01-01",
        )
    )

    assert 'document_type in ["html", "pdf"]' in expression
    assert 'validity_status in ["有效"]' in expression
    assert '\\" or true' in expression
    assert 'published_at >= "2020-01-01"' in expression


def test_store_validates_contract_and_runs_hybrid_rrf() -> None:
    async def scenario() -> tuple[
        list,
        FakeMilvusClient,
    ]:
        client = FakeMilvusClient()
        store = MilvusHybridSearchStore(
            MilvusReadConfig(
                uri="http://milvus.invalid",
                database="aisv",
                collection="spb_policy_chunks",
            ),
            client=client,
        )
        await store.initialize()
        hits = await store.hybrid_search(
            SearchQuery(
                text="如何经营快递业务？",
                top_k=5,
                candidate_k=20,
                filters=SearchFilters(validity_statuses=("有效",)),
            ),
            [0.0] * 768,
        )
        await store.close()
        return hits, client

    hits, client = asyncio.run(scenario())

    assert client.loaded is True
    assert client.closed is True
    assert client.search_kwargs is not None
    requests = client.search_kwargs["reqs"]
    assert [request.anns_field for request in requests] == [
        "text_dense",
        "text_sparse",
    ]
    assert all(request.limit == 20 for request in requests)
    assert client.search_kwargs["limit"] == 5
    assert hits[0].chunk_id == "chunk-1"
    assert hits[0].document_no == "国务院令第697号"
