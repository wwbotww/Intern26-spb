"""Owned, opt-in single-process V2 composition; no Fake or live startup probes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta

import httpx
from pydantic import ValidationError

from .adapters.postal_tracking import PostalTrackingGateway
from .adapters.postal_tracking_contract import PostalTrackingConfig
from .api.agent_contracts import (
    AgentApiDependencies,
    AgentApiDependencyFactory,
    AgentReadinessProbe,
)
from .domain.models import QueryMode
from .domain.ports import AssistantTool
from .observability.metrics import ServiceMetrics
from .observability.telemetry import create_workflow_telemetry
from .query_model import create_query_understander
from .settings import AssistantSettings
from .services.product_price_query import ProductPriceQueryService
from .tools.unavailable import UnavailableTool
from .workflow.composition import create_persistent_agent


def configured_tracking(settings: AssistantSettings) -> PostalTrackingConfig | None:
    if not settings.tracking_enabled:
        return None
    try:
        return PostalTrackingConfig(
            enabled=True,
            base_url=settings.tracking_base_url,
            path=settings.tracking_path,
            send_id=settings.tracking_send_id,
            receive_id=settings.tracking_receive_id,
            msg_kind=settings.tracking_msg_kind,
            signing_system_id=settings.tracking_signing_system_id,
            expected_response_receive_id=(
                settings.tracking_expected_response_receive_id
            ),
            timezone=settings.tracking_timezone,
            province_no=settings.tracking_province_no,
            profile=settings.tracking_profile,
            timeout_seconds=settings.tracking_timeout_seconds,
            max_connections=min(settings.max_concurrency, 32),
            max_response_bytes=settings.tracking_max_response_bytes,
        )
    except ValidationError:
        # Never echo supplied URLs, IDs, signing material, or a full settings dump.
        raise ValueError("轨迹配置不完整或不受支持；请检查 T3 配置清单") from None


class _ConfiguredReadiness:
    def __init__(
        self,
        persistence: AgentReadinessProbe,
        tracking: PostalTrackingGateway | None,
        legacy_tools: Mapping[QueryMode, AssistantTool],
        product_price_service: ProductPriceQueryService | None = None,
    ) -> None:
        self._persistence = persistence
        self._tracking = tracking
        self._legacy_tools = legacy_tools
        self._product_price = product_price_service

    async def check(self) -> Mapping[str, str]:
        checks = dict(await self._persistence.check())
        if self._tracking is not None:
            checks["capability.tracking"] = await self._tracking.readiness()
        for mode, tool in self._legacy_tools.items():
            checks[f"capability.{mode.value}"] = tool.readiness()
        if self._product_price is not None:
            checks["capability.product_price"] = self._product_price.readiness()
        return checks


def create_configured_agent_factory(
    *,
    settings: AssistantSettings,
    legacy_tools: Mapping[QueryMode, AssistantTool],
    metrics: ServiceMetrics,
    tracking_transport: httpx.AsyncBaseTransport | None = None,
    query_model_transport: httpx.AsyncBaseTransport | None = None,
    product_price_service: ProductPriceQueryService | None = None,
) -> AgentApiDependencyFactory:
    # Revalidate even programmatically copied settings; fail before any I/O.
    settings = AssistantSettings.model_validate(settings.model_dump())
    if not settings.agent_enabled:
        raise ValueError("受控 V2 尚未启用")
    tracking_config = configured_tracking(settings)
    borrowed = {
        mode: tool
        for mode, tool in legacy_tools.items()
        if not isinstance(tool, UnavailableTool)
    }

    @asynccontextmanager
    async def dependencies() -> AsyncIterator[AgentApiDependencies]:
        async with AsyncExitStack() as stack:
            tracking = None
            if tracking_config is not None:
                tracking = PostalTrackingGateway(
                    tracking_config,
                    transport=tracking_transport,
                )
                stack.push_async_callback(tracking.close)
            telemetry = await stack.enter_async_context(
                create_workflow_telemetry(settings, metrics=metrics)
            )
            understander = await stack.enter_async_context(
                create_query_understander(
                    settings,
                    transport=query_model_transport,
                    product_price_enabled=settings.price_data_model == "catalog_v2",
                )
            )
            components = await stack.enter_async_context(
                create_persistent_agent(
                    database_path=settings.agent_database_path,
                    managed_storage=settings.agent_managed_storage_enabled,
                    tracking_gateway=tracking,
                    # Missing shipping contracts remain absent, never Fake fallback.
                    policy_tool=borrowed.get(QueryMode.POLICY),
                    device_price_tool=(
                        borrowed.get(QueryMode.DEVICE_PRICE)
                        if settings.price_data_model != "catalog_v2"
                        else None
                    ),
                    product_price_service=product_price_service,
                    understander=understander,
                    telemetry=telemetry,
                    conversation_ttl=timedelta(
                        seconds=settings.agent_conversation_ttl_seconds,
                    ),
                    request_timeout_seconds=settings.agent_request_timeout_seconds,
                )
            )
            yield AgentApiDependencies(
                service=components.service,
                capabilities=components.runtime.capability_descriptors,
                readiness_probe=_ConfiguredReadiness(
                    components.readiness,
                    tracking,
                    borrowed,
                    product_price_service,
                ),
                janitor=components.janitor,
                run_timeout_seconds=settings.agent_request_timeout_seconds + 5,
                product_price_enabled=settings.price_data_model == "catalog_v2",
            )

    return dependencies
