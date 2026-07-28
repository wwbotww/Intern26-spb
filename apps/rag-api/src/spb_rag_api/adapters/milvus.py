from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from spb_contracts import (
    DENSE_FIELD,
    M3E_BASE_CONTRACT,
    REQUIRED_FIELDS,
    SPARSE_FIELD,
)

from ..domain.exceptions import (
    CollectionContractError,
    RetrievalNotReadyError,
)
from ..domain.models import SearchFilters, SearchHit, SearchQuery


OUTPUT_FIELDS = [
    "document_id",
    "parent_document_id",
    "title",
    "text",
    "source_url",
    "document_type",
    "published_at",
    "document_no",
    "source_org",
    "validity_status",
    "section_path",
    "chunk_index",
]


@dataclass(frozen=True)
class MilvusReadConfig:
    uri: str
    database: str
    collection: str
    token: str = ""
    timeout_seconds: float = 10.0
    consistency_level: str = "Bounded"
    rrf_k: int = 60
    dense_ef: int = 64


def build_filter_expression(filters: SearchFilters) -> str:
    conditions: list[str] = []
    if filters.document_types:
        conditions.append(
            f"document_type in {json.dumps(filters.document_types, ensure_ascii=False)}"
        )
    if filters.validity_statuses:
        conditions.append(
            "validity_status in "
            f"{json.dumps(filters.validity_statuses, ensure_ascii=False)}"
        )
    if filters.source_orgs:
        conditions.append(
            f"source_org in {json.dumps(filters.source_orgs, ensure_ascii=False)}"
        )
    if filters.published_from:
        conditions.append(
            "published_at >= "
            f"{json.dumps(filters.published_from, ensure_ascii=False)}"
        )
    if filters.published_through:
        conditions.append(
            "published_at <= "
            f"{json.dumps(filters.published_through, ensure_ascii=False)}"
        )
    return " and ".join(conditions)


class MilvusHybridSearchStore:
    def __init__(
        self,
        config: MilvusReadConfig,
        *,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._ready = False

    async def initialize(self) -> None:
        if self._client is None:
            self._client = await asyncio.to_thread(self._create_client)
        await asyncio.to_thread(self._validate_and_load)
        self._ready = True

    def _create_client(self) -> Any:
        from pymilvus import MilvusClient

        kwargs: dict[str, Any] = {
            "uri": self._config.uri,
            "db_name": self._config.database,
            "timeout": self._config.timeout_seconds,
        }
        if self._config.token:
            kwargs["token"] = self._config.token
        return MilvusClient(**kwargs)

    def _validate_and_load(self) -> None:
        client = self._require_client()
        collection = self._config.collection
        if not client.has_collection(collection):
            raise CollectionContractError(
                f"{self._config.database}.{collection} 不存在"
            )
        description = client.describe_collection(collection)
        fields = {
            field["name"]: field
            for field in description.get("fields", [])
        }
        missing = REQUIRED_FIELDS.difference(fields)
        if missing:
            raise CollectionContractError(
                f"collection 缺少字段：{sorted(missing)}"
            )
        dense_dimension = int(
            fields[DENSE_FIELD].get("params", {}).get("dim", 0)
        )
        if dense_dimension != M3E_BASE_CONTRACT.dimension:
            raise CollectionContractError(
                f"{DENSE_FIELD} 维度为 {dense_dimension}，"
                f"预期 {M3E_BASE_CONTRACT.dimension}"
            )
        indexes = set(client.list_indexes(collection))
        required_indexes = {"text_dense_index", "text_sparse_index"}
        if not required_indexes.issubset(indexes):
            raise CollectionContractError(
                f"collection 缺少索引：{sorted(required_indexes - indexes)}"
            )
        expected_indexes = {
            "text_dense_index": (
                DENSE_FIELD,
                M3E_BASE_CONTRACT.metric,
            ),
            "text_sparse_index": (SPARSE_FIELD, "BM25"),
        }
        for name, (expected_field, expected_metric) in (
            expected_indexes.items()
        ):
            index = client.describe_index(collection, index_name=name)
            if (
                index.get("field_name") != expected_field
                or index.get("metric_type") != expected_metric
            ):
                raise CollectionContractError(
                    f"{name} 与契约不一致："
                    f"field={index.get('field_name')}，"
                    f"metric={index.get('metric_type')}"
                )
        client.load_collection(
            collection_name=collection,
            timeout=self._config.timeout_seconds,
        )

    async def hybrid_search(
        self,
        query: SearchQuery,
        dense_vector: Sequence[float],
    ) -> list[SearchHit]:
        if not self._ready:
            raise RetrievalNotReadyError("Milvus 检索适配器尚未初始化")
        if len(dense_vector) != M3E_BASE_CONTRACT.dimension:
            raise CollectionContractError(
                f"查询向量维度为 {len(dense_vector)}，"
                f"预期 {M3E_BASE_CONTRACT.dimension}"
            )
        return await asyncio.to_thread(
            self._hybrid_search_sync,
            query,
            list(dense_vector),
        )

    def _hybrid_search_sync(
        self,
        query: SearchQuery,
        dense_vector: list[float],
    ) -> list[SearchHit]:
        from pymilvus import AnnSearchRequest, RRFRanker

        expression = build_filter_expression(query.filters)
        dense_request = AnnSearchRequest(
            data=[dense_vector],
            anns_field=DENSE_FIELD,
            param={
                "metric_type": M3E_BASE_CONTRACT.metric,
                "params": {"ef": self._config.dense_ef},
            },
            limit=query.candidate_k,
            expr=expression,
        )
        sparse_request = AnnSearchRequest(
            data=[query.text],
            anns_field=SPARSE_FIELD,
            param={"metric_type": "BM25", "params": {}},
            limit=query.candidate_k,
            expr=expression,
        )
        results = self._require_client().hybrid_search(
            collection_name=self._config.collection,
            reqs=[dense_request, sparse_request],
            ranker=RRFRanker(self._config.rrf_k),
            limit=query.top_k,
            output_fields=OUTPUT_FIELDS,
            timeout=self._config.timeout_seconds,
            consistency_level=self._config.consistency_level,
        )
        if not results:
            return []
        return [
            self._to_search_hit(hit)
            for hit in results[0]
        ]

    @staticmethod
    def _to_search_hit(hit: dict[str, Any]) -> SearchHit:
        entity = hit.get("entity") or {}
        return SearchHit(
            chunk_id=str(hit.get("id") or entity.get("id") or ""),
            document_id=str(entity.get("document_id") or ""),
            parent_document_id=str(
                entity.get("parent_document_id") or ""
            ),
            title=str(entity.get("title") or ""),
            text=str(entity.get("text") or ""),
            source_url=str(entity.get("source_url") or ""),
            document_type=str(entity.get("document_type") or ""),
            published_at=str(entity.get("published_at") or ""),
            document_no=str(entity.get("document_no") or ""),
            source_org=str(entity.get("source_org") or ""),
            validity_status=str(
                entity.get("validity_status") or "unknown"
            ),
            section_path=str(entity.get("section_path") or ""),
            chunk_index=int(entity.get("chunk_index") or 0),
            score=float(hit.get("distance", hit.get("score", 0.0))),
        )

    def _require_client(self) -> Any:
        if self._client is None:
            raise RetrievalNotReadyError("Milvus client 尚未初始化")
        return self._client

    async def close(self) -> None:
        self._ready = False
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None
