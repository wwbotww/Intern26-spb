"""Explicit, authenticated synthetic postage composition; never used by main.

No dotenv, ambient configuration, real HTTP transport, model or exporter.
The caller supplies a reviewed synthetic catalog/profile and a fresh MockTransport
per lifespan. This is a contract exercise, not a tariff simulator or live switch.
"""

from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from pydantic_settings import SettingsConfigDict

from .adapters.postal_postage import PostalPostageGateway
from .adapters.postal_postage_contract import PostalPostageConfig
from .api.agent_contracts import AgentApiDependencies, AgentReadinessProbe
from .api.app import create_app
from .services.dispatcher import EXPECTED_TOOL_NAMES
from .services.postage_preflight import PostageCatalog
from .services.query_understanding import RuleBasedQueryUnderstander
from .security.browser_session import BrowserSessionConfig
from .settings import AssistantSettings
from .tools.unavailable import UnavailableTool
from .workflow.composition import create_persistent_agent


class _OfflineSettings(AssistantSettings):
    model_config = SettingsConfigDict(env_file=None)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (init_settings,)


class _OfflineReadiness:
    def __init__(self, persistence: AgentReadinessProbe, postage: PostalPostageGateway):
        self._persistence = persistence
        self._postage = postage

    async def check(self) -> Mapping[str, str]:
        return {
            **dict(await self._persistence.check()),
            "capability.postage": await self._postage.readiness(),
        }


def create_offline_postage_app(
    *, database_path: Path, api_keys: str, config: PostalPostageConfig,
    catalog: PostageCatalog, transport_factory: Callable[[], httpx.MockTransport],
    browser_session: BrowserSessionConfig | None = None,
) -> FastAPI:
    """Own the gateway, SQLite, janitor and authentication within one process."""
    config = PostalPostageConfig.model_validate(config.model_dump())
    if not config.enabled or not config.profile.pricing_scope_ref:
        raise ValueError("P3 requires explicit synthetic configuration and pricing_scope_ref")
    preflight = config.profile.bind(catalog, require_confirmation=True)
    settings = _OfflineSettings(
        agent_enabled=True, agent_database_path=str(database_path),
        auth_enabled=True, api_keys=api_keys, rate_limit_requests=600,
        service_name="spb-postage-offline-synthetic", host="127.0.0.1",
        agent_browser_session_enabled=browser_session is not None,
        agent_browser_public_origin=browser_session.public_origin if browser_session else "",
        agent_browser_proxy_api_key=browser_session.proxy_api_key if browser_session else "",
        agent_browser_signing_key=browser_session.signing_key if browser_session else "",
        agent_browser_previous_signing_key=browser_session.previous_signing_key if browser_session else "",
        agent_browser_cookie_secure=browser_session.secure if browser_session else True,
        agent_browser_session_ttl_seconds=browser_session.ttl_seconds if browser_session else 1800,
    )

    @asynccontextmanager
    async def dependencies():
        async with AsyncExitStack() as stack:
            gateway = PostalPostageGateway(
                config, preflight=preflight, transport=transport_factory(),
            )
            stack.push_async_callback(gateway.close)
            components = await stack.enter_async_context(create_persistent_agent(
                database_path=database_path, postage_gateway=gateway,
                postage_preflight=preflight, understander=RuleBasedQueryUnderstander(),
            ))
            yield AgentApiDependencies(
                service=components.service, capabilities=components.runtime.capability_descriptors,
                readiness_probe=_OfflineReadiness(components.readiness, gateway),
                janitor=components.janitor,
            )

    return create_app(
        settings=settings, agent_api_factory=dependencies,
        tools={mode: UnavailableTool(name) for mode, name in EXPECTED_TOOL_NAMES.items()},
    )
