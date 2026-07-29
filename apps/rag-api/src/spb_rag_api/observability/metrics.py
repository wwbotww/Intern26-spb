from __future__ import annotations

from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


class ServiceMetrics:
    content_type = CONTENT_TYPE_LATEST

    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "spb_http_requests_total",
            "HTTP requests completed by the RAG API.",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "spb_http_request_duration_seconds",
            "End-to-end HTTP request duration, including SSE streams.",
            ("method", "route"),
            registry=self.registry,
            buckets=(
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1,
                2.5,
                5,
                10,
                30,
                60,
                120,
            ),
        )
        self.in_flight = Gauge(
            "spb_http_requests_in_flight",
            "HTTP requests currently being processed.",
            registry=self.registry,
        )
        self.auth_failures = Counter(
            "spb_auth_failures_total",
            "Rejected API authentication attempts.",
            registry=self.registry,
        )
        self.rate_limit_rejections = Counter(
            "spb_rate_limit_rejections_total",
            "Requests rejected by the local rate limiter.",
            registry=self.registry,
        )
        self.deepseek_tokens = Counter(
            "spb_deepseek_tokens_total",
            "Token usage reported by DeepSeek.",
            ("type",),
            registry=self.registry,
        )

    def observe_tokens(self, usage: dict[str, Any]) -> None:
        fields = {
            "prompt": "prompt_tokens",
            "completion": "completion_tokens",
            "total": "total_tokens",
            "cache_hit": "prompt_cache_hit_tokens",
            "cache_miss": "prompt_cache_miss_tokens",
        }
        for label, field in fields.items():
            value = usage.get(field)
            if isinstance(value, (int, float)) and value >= 0:
                self.deepseek_tokens.labels(type=label).inc(value)

    def render(self) -> bytes:
        return generate_latest(self.registry)
