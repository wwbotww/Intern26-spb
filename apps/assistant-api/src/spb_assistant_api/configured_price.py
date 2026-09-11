"""Explicit price-data composition; the application owns the V2 repository.

Constructing this bundle performs no I/O. Tool wrappers and the Agent borrow
the service; only the application's lifespan initializes/closes the repository.
An absent V2 DSN disables price reads, never selects the legacy SQL path.
"""

from __future__ import annotations

from dataclasses import dataclass

from .adapters.mysql_product_price import MySQLProductPriceRepository
from .domain.ports import ProductPriceReadRepository
from .services.product_price_query import ProductPriceQueryService
from .settings import AssistantSettings
from .tools.v2_device_price import V2DevicePriceTool


@dataclass(frozen=True, slots=True)
class ConfiguredProductPrice:
    repository: ProductPriceReadRepository
    service: ProductPriceQueryService
    tool: V2DevicePriceTool


def configured_product_price(
    settings: AssistantSettings,
    *,
    repository: ProductPriceReadRepository | None = None,
) -> ConfiguredProductPrice | None:
    # Revalidate copied settings before creating any owned resource.
    settings = AssistantSettings.model_validate(settings.model_dump())
    if settings.price_data_model != "catalog_v2":
        if repository is not None:
            raise ValueError("注入 V2 价格 Repository 必须显式选择 catalog_v2")
        return None
    if repository is None:
        dsn = settings.mysql_dsn.get_secret_value().strip()
        if not dsn:
            return None
        repository = MySQLProductPriceRepository(
            dsn=dsn,
            pool_size=settings.mysql_pool_size,
            connect_timeout_seconds=settings.mysql_connect_timeout_seconds,
            query_timeout_seconds=settings.mysql_query_timeout_seconds,
        )
    service = ProductPriceQueryService(
        repository,
        product_limit=settings.price_v2_product_limit,
        per_product_limit=settings.price_v2_per_product_limit,
        result_limit=settings.price_result_limit,
        match_threshold=settings.price_match_threshold,
    )
    return ConfiguredProductPrice(repository, service, V2DevicePriceTool(service=service))
