from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AssistantSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASSISTANT_",
        env_file=("apps/assistant-api/.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "spb-assistant-api"
    host: str = "0.0.0.0"
    port: int = Field(default=8081, ge=1, le=65535)
    max_concurrency: int = Field(default=5, ge=1, le=100)
    log_level: str = "info"
    log_json: bool = True

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

    rag_base_url: str = ""
    rag_api_key: SecretStr = SecretStr("")
    rag_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    rag_health_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    rag_top_k: int = Field(default=5, ge=1, le=100)
    rag_candidate_k: int = Field(default=40, ge=1, le=500)
    rag_verify_tls: bool = True

    mysql_dsn: SecretStr = SecretStr("")
    mysql_pool_size: int = Field(default=5, ge=1, le=20)
    mysql_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
    )
    mysql_query_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )
    price_candidate_limit: int = Field(default=5000, ge=1, le=10000)
    price_result_limit: int = Field(default=50, ge=1, le=100)
    price_match_threshold: float = Field(default=65.0, ge=0, le=100)

    @model_validator(mode="after")
    def validate_dependency_settings(self) -> "AssistantSettings":
        rag_base_url = self.rag_base_url.strip()
        if rag_base_url:
            parsed_url = urlsplit(rag_base_url)
            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
                or parsed_url.username is not None
                or parsed_url.password is not None
                or parsed_url.query
                or parsed_url.fragment
            ):
                raise ValueError(
                    "rag_base_url 必须是无凭证、查询参数和片段的 HTTP(S) URL"
                )
        if self.rag_top_k > self.rag_candidate_k:
            raise ValueError("rag_top_k 不能大于 rag_candidate_k")
        dsn = self.mysql_dsn.get_secret_value().strip()
        if dsn and not dsn.startswith("mysql+pymysql://"):
            raise ValueError(
                "mysql_dsn 必须使用 mysql+pymysql:// 驱动"
            )
        if self.price_result_limit > self.price_candidate_limit:
            raise ValueError(
                "price_result_limit 不能大于 price_candidate_limit"
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
