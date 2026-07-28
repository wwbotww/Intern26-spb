from __future__ import annotations


SCHEMA_VERSION = 1
DATABASE_NAME = "aisv"
COLLECTION_NAME = "spb_policy_chunks"

PRIMARY_FIELD = "id"
TEXT_FIELD = "text"
DENSE_FIELD = "text_dense"
SPARSE_FIELD = "text_sparse"

REQUIRED_FIELDS = frozenset(
    {
        PRIMARY_FIELD,
        "document_id",
        "parent_document_id",
        "title",
        TEXT_FIELD,
        "embedding_text",
        DENSE_FIELD,
        SPARSE_FIELD,
        "source_url",
        "source_host",
        "document_type",
        "published_at",
        "document_no",
        "source_org",
        "validity_status",
        "section_path",
        "chunk_index",
        "content_hash",
        "fetch_status",
    }
)
