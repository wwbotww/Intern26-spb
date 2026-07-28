from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "spb-rag-api"
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    max_concurrency: int = Field(default=5, ge=1, le=100)
    log_level: str = "info"
