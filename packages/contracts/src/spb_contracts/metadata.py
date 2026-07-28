from __future__ import annotations

from typing import TypedDict


class ChunkMetadata(TypedDict):
    chunk_id: str
    document_id: str
    parent_document_id: str | None
    title: str
    chunk_text: str
    embedding_input: str
    source_url: str
    source_host: str
    document_type: str
    published_at: str
    document_no: str
    source_org: str
    validity_status: str
    section_path: str
    chunk_index: int
    content_hash: str
    fetch_status: str
