from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

from .adapters.deepseek_understanding import DeepSeekQueryUnderstandingModel
from .domain.ports import StructuredQueryUnderstandingModel
from .services.query_understanding import (
    HybridQueryUnderstander,
    RuleBasedQueryUnderstander,
    StructuredLlmQueryUnderstander,
)
from .settings import AssistantSettings


@asynccontextmanager
async def create_query_understander(
    settings: AssistantSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    product_price_enabled: bool = False,
    decorate_model: Callable[
        [StructuredQueryUnderstandingModel], StructuredQueryUnderstandingModel
    ]
    | None = None,
    call_observer: Callable[[Mapping[str, Any]], None] | None = None,
) -> AsyncIterator[HybridQueryUnderstander]:
    """Own the optional provider lifecycle outside Domain, Service and Graph."""

    if not settings.query_model_enabled:
        yield HybridQueryUnderstander(rules=RuleBasedQueryUnderstander(product_price_enabled=product_price_enabled))
        return

    model = DeepSeekQueryUnderstandingModel(
        base_url=settings.query_model_base_url,
        api_key=settings.query_model_api_key.get_secret_value(),
        model=settings.query_model_name,
        timeout_seconds=settings.query_model_timeout_seconds,
        max_tokens=settings.query_model_max_tokens,
        max_concurrency=settings.query_model_max_concurrency,
        max_response_bytes=settings.query_model_max_response_bytes,
        transport=transport,
        call_observer=call_observer,
        product_price_enabled=product_price_enabled,
    )
    try:
        yield HybridQueryUnderstander(
            rules=RuleBasedQueryUnderstander(product_price_enabled=product_price_enabled),
            model_fallback=StructuredLlmQueryUnderstander(
                decorate_model(model) if decorate_model else model,
                product_price_enabled=product_price_enabled,
            )
        )
    finally:
        await model.close()
