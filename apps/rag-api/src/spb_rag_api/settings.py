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
    log_json: bool = True
    initialize_on_startup: bool = True

    auth_enabled: bool = True
    api_keys: SecretStr = SecretStr("")
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=60, ge=1, le=100000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    max_request_body_bytes: int = Field(
        default=1_048_576,
        ge=1024,
        le=16_777_216,
    )
    metrics_enabled: bool = True

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

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_thinking: Literal["enabled", "disabled"] = "disabled"
    deepseek_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    deepseek_max_tokens: int = Field(default=1200, ge=1, le=32768)
    deepseek_temperature: float = Field(default=0.1, ge=0, le=2)
    chat_context_max_chars: int = Field(
        default=16000,
        ge=1000,
        le=200000,
    )

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

    def parsed_api_keys(self) -> tuple[str, ...]:
        value = self.api_keys.get_secret_value()
        return tuple(
            dict.fromkeys(
                item.strip()
                for item in value.split(",")
                if item.strip()
            )
        )
