from __future__ import annotations

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
            "assistant_http_requests_total",
            "HTTP requests completed by the assistant API.",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "assistant_http_request_duration_seconds",
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
            "assistant_http_requests_in_flight",
            "HTTP requests currently handled by the assistant API.",
            registry=self.registry,
        )
        self.auth_failures = Counter(
            "assistant_auth_failures_total",
            "Rejected assistant API authentication attempts.",
            registry=self.registry,
        )
        self.rate_limit_rejections = Counter(
            "assistant_rate_limit_rejections_total",
            "Requests rejected by the assistant API rate limiter.",
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
