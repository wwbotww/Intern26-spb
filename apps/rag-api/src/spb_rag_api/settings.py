from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from spb_contracts import COLLECTION_NAME, DATABASE_NAME, M3E_BASE_CONTRACT


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=("apps/rag-api/.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "spb-rag-api"
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    max_concurrency: int = Field(default=5, ge=1, le=100)
    log_level: str = "info"
    initialize_on_startup: bool = True

    milvus_uri: str = ""
    milvus_token: SecretStr = SecretStr("")
    milvus_database: str = DATABASE_NAME
    milvus_collection: str = COLLECTION_NAME
    milvus_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    milvus_consistency_level: Literal[
        "Strong", "Bounded", "Session", "Eventually"
    ] = "Bounded"

    embedding_model: str = M3E_BASE_CONTRACT.model
    embedding_device: Literal["cpu", "cuda", "mps"] = "cpu"

    search_default_top_k: int = Field(default=8, ge=1, le=100)
    search_max_top_k: int = Field(default=20, ge=1, le=100)
    search_candidate_k: int = Field(default=40, ge=1, le=500)
    search_max_candidate_k: int = Field(default=100, ge=1, le=500)
    search_rrf_k: int = Field(default=60, ge=1, le=1000)
    search_dense_ef: int = Field(default=64, ge=1, le=4096)

    @model_validator(mode="after")
    def validate_search_limits(self) -> "ApiSettings":
        if self.search_default_top_k > self.search_max_top_k:
            raise ValueError(
                "search_default_top_k 不能大于 search_max_top_k"
            )
        if self.search_candidate_k < self.search_max_top_k:
            raise ValueError(
                "search_candidate_k 不能小于 search_max_top_k"
            )
        if self.search_candidate_k > self.search_max_candidate_k:
            raise ValueError(
                "search_candidate_k 不能大于 search_max_candidate_k"
            )
        return self
