"""Explicit single-instance deployment entry; no dotenv or synthetic fallback."""

from typing import Literal

import uvicorn
from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from .api.app import create_app
from .observability.logging import configure_logging
from .settings import AssistantSettings
from .security.browser_session import private_http_origin


class DeploymentSettings(AssistantSettings):
    model_config = SettingsConfigDict(env_file=None)
    deployment_ui_mode: Literal["agent", "legacy"] = "agent"
    deployment_transport_mode: Literal["https", "private-http"] = "https"

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return init_settings, env_settings

    @model_validator(mode="after")
    def validate_deployment(self):
        if not self.auth_enabled or not self.parsed_api_keys():
            raise ValueError("受控部署必须启用服务鉴权")
        if self.deployment_transport_mode == "private-http":
            if (
                not self.agent_browser_private_http_enabled or self.agent_browser_cookie_secure
                or not private_http_origin(self.agent_browser_public_origin)
            ):
                raise ValueError("内网 HTTP 部署必须显式开启兼容模式并限定私有 IPv4 origin")
        elif self.agent_browser_private_http_enabled or not self.agent_browser_cookie_secure:
            raise ValueError("默认 HTTPS 部署不能降级浏览器 Cookie")
        enabled = (self.agent_enabled, self.agent_managed_storage_enabled, self.agent_browser_session_enabled)
        if self.deployment_ui_mode == "agent":
            if not all(enabled):
                raise ValueError("Agent 部署要求受控存储及浏览器身份")
        elif any(enabled) or self.tracking_enabled:
            raise ValueError("V1 回退要求关闭 V2、浏览器身份及轨迹；不会删除存储")
        return self


def build_app():
    settings = DeploymentSettings()
    return create_app(settings=settings)


def main():
    settings = DeploymentSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    uvicorn.run(
        create_app(settings=settings), host=settings.host, port=settings.port,
        workers=1, proxy_headers=False, access_log=False, log_config=None,
    )


if __name__ == "__main__":
    main()
