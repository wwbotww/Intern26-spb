from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .security.browser_session import BrowserSessionConfig


class AssistantSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASSISTANT_",
        env_file=("apps/assistant-api/.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
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

    # Opt-in single-process V2 composition. No temporary/implicit state store.
    agent_enabled: bool = False
    agent_database_path: str = Field(default="", repr=False)
    agent_managed_storage_enabled: bool = False
    agent_conversation_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    agent_request_timeout_seconds: float = Field(default=30, gt=0, le=120)

    # Anonymous visitor isolation, not user login. Requires a dedicated gateway role.
    agent_browser_session_enabled: bool = False
    agent_browser_proxy_api_key: SecretStr = SecretStr("")
    agent_browser_signing_key: SecretStr = SecretStr("")
    agent_browser_previous_signing_key: SecretStr = SecretStr("")
    agent_browser_public_origin: str = ""
    agent_browser_cookie_secure: bool = True
    agent_browser_private_http_enabled: bool = False
    agent_browser_session_ttl_seconds: int = Field(default=1800, ge=60, le=86400)

    tracking_enabled: bool = False
    tracking_base_url: str = Field(default="", repr=False)
    tracking_path: str = Field(default="interface", repr=False)
    tracking_send_id: str = Field(default="", repr=False)
    tracking_receive_id: str = Field(default="JDPT", repr=False)
    tracking_msg_kind: str = Field(default="", repr=False)
    tracking_signing_system_id: SecretStr = SecretStr("")
    tracking_expected_response_receive_id: str = Field(default="", repr=False)
    tracking_timezone: str = ""
    tracking_province_no: str = "99"
    tracking_profile: str = ""
    tracking_timeout_seconds: float = Field(default=5, gt=0, le=30)
    tracking_max_response_bytes: int = Field(default=1048576, ge=1, le=1048576)

    # Only explicitly composed V2 runtimes use this owned telemetry pipeline.
    otel_enabled: bool = False
    otel_endpoint: str = "http://127.0.0.1:4318/v1/traces"
    otel_sample_ratio: float = Field(default=0.1, ge=0, le=1)
    otel_export_timeout_seconds: float = Field(default=2, gt=0, le=10)

    # Optional semantic fallback for the explicitly composed V2 Agent.
    query_model_enabled: bool = False
    query_model_base_url: str = "https://api.deepseek.com"
    query_model_api_key: SecretStr = SecretStr("")
    query_model_name: str = Field(
        default="deepseek-v4-flash", min_length=1, max_length=128
    )
    query_model_timeout_seconds: float = Field(default=8.0, gt=0, le=20)
    query_model_max_tokens: int = Field(default=768, ge=128, le=4096)
    query_model_max_response_bytes: int = Field(
        default=65536, ge=1024, le=262144
    )
    query_model_max_concurrency: int = Field(default=2, ge=1, le=20)

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
        if self.agent_managed_storage_enabled and not self.agent_enabled:
            raise ValueError("受控存储必须同时启用 V2 Agent")
        if self.agent_browser_session_enabled:
            if not self.agent_enabled:
                raise ValueError("浏览器访客隔离必须同时启用受控 V2")
            config = self.browser_session_config()
            if config.proxy_api_key not in self.parsed_api_keys():
                raise ValueError("浏览器代理 Key 必须包含在服务 API Key 列表中")
            if config.signing_key in self.parsed_api_keys() or config.previous_signing_key in self.parsed_api_keys():
                raise ValueError("浏览器签名 Key 不能复用任何服务 API Key")
        if self.tracking_enabled and not self.agent_enabled:
            raise ValueError("启用轨迹装配必须同时启用 ASSISTANT_AGENT_ENABLED")
        if self.agent_enabled:
            if not self.auth_enabled or not self.parsed_api_keys():
                raise ValueError("受控 V2 必须启用鉴权并配置 API Key")
            if not Path(self.agent_database_path).is_absolute():
                raise ValueError("受控 V2 必须显式配置绝对 SQLite 文件路径")
        if self.otel_enabled:
            parsed_otel_url = urlsplit(self.otel_endpoint.strip())
            if (
                parsed_otel_url.scheme not in {"http", "https"}
                or not parsed_otel_url.hostname
                or parsed_otel_url.username is not None
                or parsed_otel_url.password is not None
                or parsed_otel_url.query
                or parsed_otel_url.fragment
                or parsed_otel_url.path != "/v1/traces"
            ):
                raise ValueError(
                    "otel_endpoint 必须是无凭据的 HTTP(S) /v1/traces URL"
                )
        if self.query_model_enabled:
            parsed_model_url = urlsplit(self.query_model_base_url.strip())
            if (
                parsed_model_url.scheme != "https"
                or not parsed_model_url.hostname
                or parsed_model_url.username is not None
                or parsed_model_url.password is not None
                or parsed_model_url.query
                or parsed_model_url.fragment
            ):
                raise ValueError(
                    "query_model_base_url 必须是无凭据、查询参数和片段的 HTTPS URL"
                )
            if not self.query_model_api_key.get_secret_value().strip():
                raise ValueError(
                    "启用模型理解时必须配置 ASSISTANT_QUERY_MODEL_API_KEY"
                )
            if not self.query_model_name.strip():
                raise ValueError("启用模型理解时 query_model_name 不能为空")
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
            raise ValueError("mysql_dsn 必须使用 mysql+pymysql:// 驱动")
        if self.price_result_limit > self.price_candidate_limit:
            raise ValueError(
                "price_result_limit 不能大于 price_candidate_limit"
            )
        return self

    def browser_session_config(self) -> BrowserSessionConfig | None:
        if not self.agent_browser_session_enabled:
            return None
        return BrowserSessionConfig(
            public_origin=self.agent_browser_public_origin,
            proxy_api_key=self.agent_browser_proxy_api_key.get_secret_value(),
            signing_key=self.agent_browser_signing_key.get_secret_value(),
            previous_signing_key=self.agent_browser_previous_signing_key.get_secret_value(),
            secure=self.agent_browser_cookie_secure,
            private_http_enabled=self.agent_browser_private_http_enabled,
            ttl_seconds=self.agent_browser_session_ttl_seconds,
        )

    def parsed_api_keys(self) -> tuple[str, ...]:
        value = self.api_keys.get_secret_value()
        return tuple(
            dict.fromkeys(
                item.strip() for item in value.split(",") if item.strip()
            )
        )
