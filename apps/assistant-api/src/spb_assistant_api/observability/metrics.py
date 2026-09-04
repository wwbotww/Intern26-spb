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
        self.agent_runs = Counter(
            "assistant_agent_runs_total",
            "Terminal V2 Agent message operations by stable public outcome.",
            ("transport", "outcome", "intent"),
            registry=self.registry,
        )
        self.agent_run_duration = Histogram(
            "assistant_agent_run_duration_seconds",
            "V2 Agent message operation duration, excluding client rendering.",
            ("transport", "outcome"),
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
            ),
        )
        self.agent_runs_in_flight = Gauge(
            "assistant_agent_runs_in_flight",
            "V2 Agent message operations currently executing.",
            registry=self.registry,
        )
        self.agent_interrupts = Counter(
            "assistant_agent_interrupts_total",
            "V2 Agent runs paused for bounded human input.",
            ("reason", "intent"),
            registry=self.registry,
        )
        self.agent_failures = Counter(
            "assistant_agent_failures_total",
            "V2 Agent terminal failures by bounded failure category.",
            ("transport", "category"),
            registry=self.registry,
        )
        self.agent_readiness = Gauge(
            "assistant_agent_readiness",
            "V2 Agent component readiness (1 ready, 0 unavailable).",
            ("component",),
            registry=self.registry,
        )
        self.agent_janitor_runs = Counter(
            "assistant_agent_janitor_runs_total",
            "Conversation janitor runs by outcome.",
            ("outcome",),
            registry=self.registry,
        )
        self.agent_janitor_duration = Histogram(
            "assistant_agent_janitor_duration_seconds",
            "Conversation janitor run duration.",
            registry=self.registry,
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
        )
        self.agent_janitor_cleaned = Counter(
            "assistant_agent_janitor_cleaned_total",
            "Records cleaned by the conversation janitor.",
            ("resource",),
            registry=self.registry,
        )

    def observe_agent_run(
        self,
        *,
        transport: str,
        outcome: str,
        intent: str,
        duration_seconds: float,
        interrupt_reason: str | None = None,
        failure_category: str | None = None,
    ) -> None:
        self.agent_runs.labels(
            transport=_bounded_transport(transport),
            outcome=_bounded_outcome(outcome),
            intent=_bounded_intent(intent),
        ).inc()
        self.agent_run_duration.labels(
            transport=_bounded_transport(transport),
            outcome=_bounded_outcome(outcome),
        ).observe(max(0.0, duration_seconds))
        if interrupt_reason is not None:
            self.agent_interrupts.labels(
                reason=_bounded_interrupt(interrupt_reason),
                intent=_bounded_intent(intent),
            ).inc()
        if failure_category is not None:
            self.agent_failures.labels(
                transport=_bounded_transport(transport),
                category=_bounded_failure_category(failure_category),
            ).inc()

    def set_agent_readiness(self, *, component: str, ready: bool) -> None:
        self.agent_readiness.labels(
            component=_bounded_readiness_component(component)
        ).set(1 if ready else 0)

    def observe_agent_janitor(
        self,
        *,
        outcome: str,
        duration_seconds: float,
        expired_conversations: int = 0,
        deleted_idempotency_receipts: int = 0,
        deleted_tool_receipts: int = 0,
    ) -> None:
        normalized_outcome = (
            outcome if outcome in {"success", "partial", "error", "timeout"}
            else "error"
        )
        self.agent_janitor_runs.labels(outcome=normalized_outcome).inc()
        self.agent_janitor_duration.observe(max(0.0, duration_seconds))
        counts = {
            "conversation": expired_conversations,
            "idempotency_receipt": deleted_idempotency_receipts,
            "tool_receipt": deleted_tool_receipts,
        }
        for resource, count in counts.items():
            if count > 0:
                self.agent_janitor_cleaned.labels(resource=resource).inc(count)

    def render(self) -> bytes:
        return generate_latest(self.registry)


_TRANSPORTS = frozenset({"json", "sse", "unknown"})
_OUTCOMES = frozenset(
    {
        "completed",
        "waiting_user",
        "handoff",
        "failed",
        "cancelled",
        "unknown",
    }
)
_INTENTS = frozenset(
    {
        "policy",
        "device_price",
        "tracking",
        "delivery_time",
        "postage",
        "unknown",
    }
)
_INTERRUPTS = frozenset(
    {"collect_slots", "clarify_intent", "unknown"}
)
_FAILURE_CATEGORIES = frozenset(
    {
        "invalid_input",
        "missing_input",
        "ambiguous_intent",
        "no_match",
        "state_conflict",
        "upstream_timeout",
        "upstream_rate_limited",
        "upstream_unavailable",
        "persistence_unavailable",
        "state_schema_incompatible",
        "contract_violation",
        "loop_budget_exceeded",
        "internal_error",
        "timeout",
        "cancelled",
        "internal",
        "unknown",
    }
)
_READINESS_COMPONENTS = frozenset(
    {
        "agent_api",
        "persistence",
        "checkpoint",
        "janitor",
        "capability_policy",
        "capability_device_price",
        "capability_tracking",
        "capability_delivery_time",
        "capability_postage",
        "unknown",
    }
)


def _bounded_transport(value: str) -> str:
    return value if value in _TRANSPORTS else "unknown"


def _bounded_outcome(value: str) -> str:
    return value if value in _OUTCOMES else "unknown"


def _bounded_intent(value: str) -> str:
    return value if value in _INTENTS else "unknown"


def _bounded_interrupt(value: str) -> str:
    return value if value in _INTERRUPTS else "unknown"


def _bounded_failure_category(value: str) -> str:
    return value if value in _FAILURE_CATEGORIES else "unknown"


def _bounded_readiness_component(value: str) -> str:
    return value if value in _READINESS_COMPONENTS else "unknown"
