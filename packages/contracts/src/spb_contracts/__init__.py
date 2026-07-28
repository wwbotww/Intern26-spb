"""离线流水线与在线 RAG 服务共享的数据契约。"""

from .collection import (
    COLLECTION_NAME,
    DATABASE_NAME,
    DENSE_FIELD,
    REQUIRED_FIELDS,
    SCHEMA_VERSION,
    SPARSE_FIELD,
    TEXT_FIELD,
)
from .embedding import EmbeddingContract, M3E_BASE_CONTRACT
from .metadata import ChunkMetadata

__all__ = [
    "COLLECTION_NAME",
    "DATABASE_NAME",
    "DENSE_FIELD",
    "EmbeddingContract",
    "M3E_BASE_CONTRACT",
    "REQUIRED_FIELDS",
    "SCHEMA_VERSION",
    "SPARSE_FIELD",
    "TEXT_FIELD",
    "ChunkMetadata",
]
