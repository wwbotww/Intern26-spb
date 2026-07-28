from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class MilvusConfig:
    uri: str
    database: str
    collection: str = "spb_policy_chunks"
    token: str = ""
    timeout: float = 30.0


class MilvusSink:
    def __init__(self, config: MilvusConfig):
        from pymilvus import MilvusClient

        self.config = config
        kwargs: dict[str, Any] = {
            "uri": config.uri,
            "db_name": config.database,
            "timeout": config.timeout,
        }
        if config.token:
            kwargs["token"] = config.token
        self.client = MilvusClient(**kwargs)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "MilvusSink":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def create_collection(self, dimension: int) -> dict[str, Any]:
        from pymilvus import DataType, Function, FunctionType, MilvusClient

        if self.client.has_collection(self.config.collection):
            raise RuntimeError(
                f"{self.config.database}.{self.config.collection} 已存在；"
                "为避免影响现有数据，拒绝覆盖"
            )
        schema = MilvusClient.create_schema(
            auto_id=False,
            enable_dynamic_field=False,
            description=(
                "国家邮政局政策法规标准正文及附件分块；"
                "dense=moka-ai/m3e-base，sparse=BM25"
            ),
        )
        schema.add_field(
            "id", DataType.VARCHAR, is_primary=True, max_length=64
        )
        schema.add_field("document_id", DataType.VARCHAR, max_length=64)
        schema.add_field("parent_document_id", DataType.VARCHAR, max_length=64)
        schema.add_field("title", DataType.VARCHAR, max_length=1024)
        schema.add_field(
            "text",
            DataType.VARCHAR,
            max_length=8192,
            enable_analyzer=True,
            enable_match=True,
            analyzer_params={"type": "chinese"},
        )
        schema.add_field("embedding_text", DataType.VARCHAR, max_length=8192)
        schema.add_field("text_dense", DataType.FLOAT_VECTOR, dim=dimension)
        schema.add_field("text_sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("source_url", DataType.VARCHAR, max_length=4096)
        schema.add_field("source_host", DataType.VARCHAR, max_length=256)
        schema.add_field("document_type", DataType.VARCHAR, max_length=32)
        schema.add_field("published_at", DataType.VARCHAR, max_length=32)
        schema.add_field("document_no", DataType.VARCHAR, max_length=512)
        schema.add_field("source_org", DataType.VARCHAR, max_length=256)
        schema.add_field("validity_status", DataType.VARCHAR, max_length=32)
        schema.add_field("section_path", DataType.VARCHAR, max_length=1024)
        schema.add_field("chunk_index", DataType.INT32)
        schema.add_field("content_hash", DataType.VARCHAR, max_length=64)
        schema.add_field("fetch_status", DataType.VARCHAR, max_length=32)
        schema.add_function(
            Function(
                name="text_bm25_emb",
                function_type=FunctionType.BM25,
                input_field_names=["text"],
                output_field_names=["text_sparse"],
            )
        )

        indexes = MilvusClient.prepare_index_params()
        indexes.add_index(
            field_name="text_dense",
            index_name="text_dense_index",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        indexes.add_index(
            field_name="text_sparse",
            index_name="text_sparse_index",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={"inverted_index_algo": "DAAT_MAXSCORE"},
        )
        for field_name in (
            "document_id",
            "parent_document_id",
            "published_at",
            "validity_status",
        ):
            indexes.add_index(
                field_name=field_name,
                index_name=field_name,
                index_type="AUTOINDEX",
            )
        self.client.create_collection(
            collection_name=self.config.collection,
            schema=schema,
            index_params=indexes,
        )
        return self.describe()

    def insert_artifact(
        self,
        chunks: list[dict[str, Any]],
        vectors: np.ndarray,
        *,
        batch_size: int = 128,
    ) -> dict[str, Any]:
        if not self.client.has_collection(self.config.collection):
            raise RuntimeError(f"{self.config.collection} 不存在")
        current_rows = int(
            self.client.get_collection_stats(self.config.collection).get(
                "row_count", 0
            )
        )
        if current_rows:
            raise RuntimeError(
                f"{self.config.collection} 已有 {current_rows} 条记录；"
                "为避免覆盖，拒绝继续写入"
            )
        if len(chunks) != len(vectors):
            raise ValueError("chunks 与 vectors 数量不一致")
        inserted = self._insert_indexes(
            chunks,
            vectors,
            list(range(len(chunks))),
            batch_size=batch_size,
        )
        return {"inserted": inserted, **self.describe()}

    @staticmethod
    def _record(chunk: dict[str, Any], vector: np.ndarray) -> dict[str, Any]:
        return {
            "id": chunk["chunk_id"],
            "document_id": chunk["document_id"],
            "parent_document_id": chunk.get("parent_document_id") or "",
            "title": chunk["title"],
            "text": chunk["chunk_text"],
            "embedding_text": chunk["embedding_input"],
            "text_dense": vector.tolist(),
            "source_url": chunk["source_url"],
            "source_host": chunk.get("source_host", ""),
            "document_type": chunk.get("document_type", ""),
            "published_at": chunk.get("published_at", ""),
            "document_no": chunk.get("document_no", ""),
            "source_org": chunk.get("source_org", ""),
            "validity_status": chunk.get("validity_status", "unknown"),
            "section_path": chunk.get("section_path", ""),
            "chunk_index": int(chunk["chunk_index"]),
            "content_hash": chunk["content_hash"],
            "fetch_status": chunk.get("fetch_status", ""),
        }

    def _insert_indexes(
        self,
        chunks: list[dict[str, Any]],
        vectors: np.ndarray,
        indexes: list[int],
        *,
        batch_size: int,
    ) -> int:
        inserted = 0
        for start in range(0, len(indexes), batch_size):
            batch_indexes = indexes[start : start + batch_size]
            records = [
                self._record(chunks[index], vectors[index])
                for index in batch_indexes
            ]
            response = self.client.insert(
                collection_name=self.config.collection,
                data=records,
            )
            inserted += int(response.get("insert_count", len(records)))
        return inserted

    def sync_artifact(
        self,
        chunks: list[dict[str, Any]],
        vectors: np.ndarray,
        *,
        batch_size: int = 128,
    ) -> dict[str, Any]:
        if len(chunks) != len(vectors):
            raise ValueError("chunks 与 vectors 数量不一致")
        iterator = self.client.query_iterator(
            collection_name=self.config.collection,
            batch_size=1000,
            filter="",
            output_fields=["id"],
            consistency_level="Strong",
        )
        existing_ids: set[str] = set()
        try:
            while batch := iterator.next():
                existing_ids.update(row["id"] for row in batch)
        finally:
            iterator.close()
        missing_indexes = [
            index
            for index, chunk in enumerate(chunks)
            if chunk["chunk_id"] not in existing_ids
        ]
        inserted = self._insert_indexes(
            chunks,
            vectors,
            missing_indexes,
            batch_size=batch_size,
        )
        return {
            "existing": len(existing_ids),
            "missing": len(missing_indexes),
            "inserted": inserted,
            **self.describe(),
        }

    def describe(self) -> dict[str, Any]:
        if not self.client.has_collection(self.config.collection):
            return {
                "database": self.config.database,
                "collection": self.config.collection,
                "exists": False,
            }
        return {
            "database": self.config.database,
            "collection": self.config.collection,
            "exists": True,
            "stats": self.client.get_collection_stats(
                self.config.collection
            ),
            "load_state": self.client.get_load_state(
                self.config.collection
            ),
            "schema": self.client.describe_collection(
                self.config.collection
            ),
            "indexes": {
                name: self.client.describe_index(
                    self.config.collection, index_name=name
                )
                for name in self.client.list_indexes(
                    self.config.collection
                )
            },
        }

    def ensure_collection(self, dimension: int) -> None:
        if not self.client.has_collection(self.config.collection):
            self.create_collection(dimension)

    def upsert(self, records: list[dict]) -> None:
        self.client.upsert(
            collection_name=self.config.collection,
            data=records,
        )
