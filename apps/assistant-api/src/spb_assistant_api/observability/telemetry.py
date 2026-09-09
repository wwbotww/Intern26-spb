from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from time import perf_counter

from opentelemetry import context, trace
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from requests import RequestException, Response, Session

from .. import __version__
from ..domain.failures import FailureCategory
from ..settings import AssistantSettings
from .metrics import (
    AGENT_NODES,
    NODE_OUTCOMES,
    WORKFLOW_OUTCOMES,
    ServiceMetrics,
)


@dataclass(slots=True)
class OperationObservation:
    # Only these bounded fields cross the runtime -> telemetry boundary.
    outcome: str = "success"
    tool_attempt: int | None = None
    tool_reused: bool | None = None
    failure_category: str | None = None


class WorkflowTelemetry:
    """Borrowed tracer + metrics; no graph state, globals or lifecycle here."""

    def __init__(
        self, metrics: ServiceMetrics, tracer: trace.Tracer | None = None
    ) -> None:
        self.metrics = metrics
        self._tracer = tracer

    def _best_effort(self, operation: str, call: Callable[[], object]) -> None:
        try:
            call()
        except Exception:
            # Never log exception text, exporter URLs or business values.
            try:
                self.metrics.observe_telemetry_error(operation)
            except Exception:
                pass

    @contextmanager
    def measure(
        self, *, node: str | None = None, resumed: bool = False
    ) -> Iterator[OperationObservation]:
        is_node = node is not None
        node = node if node in AGENT_NODES else "unknown"
        mode = "resume" if resumed else "start"
        observation = OperationObservation()
        span = None
        token = None
        started = perf_counter()
        if self._tracer is not None:
            try:
                # Every invocation is a new root, including resume. Do not
                # inherit untrusted HTTP traceparent/baggage or persist IDs.
                parent = (
                    context.get_current() if is_node else context.Context()
                )
                span = self._tracer.start_span(
                    f"agent.node.{node}" if is_node else "agent.workflow",
                    context=parent,
                    attributes=(
                        {"agent.node.name": node}
                        if is_node
                        else {"agent.workflow.mode": mode}
                    ),
                    record_exception=False,
                    set_status_on_exception=False,
                )
                token = context.attach(trace.set_span_in_context(span, parent))
            except Exception:
                self._best_effort(
                    "start",
                    lambda: self.metrics.observe_telemetry_error("start"),
                )
        try:
            yield observation
        except asyncio.CancelledError:
            observation.outcome = "cancelled"
            raise
        except Exception:
            if observation.outcome != "interrupted":
                observation.outcome = "error"
            raise
        finally:
            duration = max(0.0, perf_counter() - started)
            allowed = NODE_OUTCOMES if is_node else WORKFLOW_OUTCOMES
            outcome = observation.outcome
            outcome = outcome if outcome in allowed else "unknown"
            if is_node:
                self._best_effort(
                    "finish",
                    lambda: self.metrics.observe_agent_node(
                        node=node, outcome=outcome, duration_seconds=duration
                    ),
                )
            else:
                self._best_effort(
                    "finish",
                    lambda: self.metrics.observe_agent_workflow(
                        mode=mode, outcome=outcome
                    ),
                )
            if span is not None:
                self._best_effort(
                    "finish", lambda: _finish_span(span, observation, outcome)
                )
            if token is not None:
                self._best_effort("finish", lambda: context.detach(token))


def _finish_span(
    span: trace.Span, observation: OperationObservation, outcome: str
) -> None:
    try:
        span.set_attribute("agent.outcome", outcome)
        if outcome in {"error", "failed"}:
            span.set_status(trace.StatusCode.ERROR)
        if type(observation.tool_attempt) is int:
            span.set_attribute(
                "agent.tool.attempt", min(8, max(1, observation.tool_attempt))
            )
        if type(observation.tool_reused) is bool:
            span.set_attribute("agent.tool.reused", observation.tool_reused)
        if observation.failure_category in {
            item.value for item in FailureCategory
        }:
            span.set_attribute(
                "agent.failure.category", observation.failure_category
            )
    finally:
        span.end()


def current_trace_fields() -> dict[str, str]:
    """Correlation for the existing sanitized workflow log, not public API."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid or not span_context.trace_flags.sampled:
        return {}
    return {
        "trace_id": f"{span_context.trace_id:032x}",
        "span_id": f"{span_context.span_id:016x}",
    }


class _LocalOTLPSession(Session):
    """No ambient proxy/credentials, redirects or response-body logging."""

    def __init__(self) -> None:
        super().__init__()
        self.trust_env = False

    def post(self, url, data=None, **kwargs):
        try:
            with super().post(
                url,
                data=data,
                timeout=kwargs.get("timeout"),
                allow_redirects=False,
                stream=True,
                verify=True,
            ) as response:
                status = response.status_code
        except RequestException:
            raise RequestException("telemetry_transport_error") from None
        # The SDK consumes only status/ok/reason, not the response body.
        sanitized = Response()
        sanitized.status_code = status if not 300 <= status < 400 else 400
        sanitized.reason = "telemetry_http_response"
        sanitized._content = b""
        sanitized._content_consumed = True
        return sanitized


class _ResilientExporter(SpanExporter):
    def __init__(
        self, delegate: SpanExporter, metrics: ServiceMetrics
    ) -> None:
        self.delegate = delegate
        self.telemetry = WorkflowTelemetry(metrics)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self.delegate.export(spans)
        except Exception:
            result = SpanExportResult.FAILURE
        self.telemetry._best_effort(
            "export",
            lambda: self.telemetry.metrics.observe_trace_export(
                success=result is SpanExportResult.SUCCESS
            ),
        )
        if result is not SpanExportResult.SUCCESS:
            self.telemetry._best_effort(
                "export",
                lambda: self.telemetry.metrics.observe_telemetry_error(
                    "export"
                ),
            )
        return result

    def shutdown(self) -> None:
        self.telemetry._best_effort("shutdown", self.delegate.shutdown)


@asynccontextmanager
async def create_workflow_telemetry(
    settings: AssistantSettings, *, metrics: ServiceMetrics
) -> AsyncIterator[WorkflowTelemetry]:
    """Own SDK resources inside lifespan, without changing global providers."""
    fallback = WorkflowTelemetry(metrics)
    if not settings.otel_enabled:
        yield fallback
        return
    provider = None
    exporter = None
    session = None
    attached = False
    try:
        try:
            provider = TracerProvider(
                resource=Resource(
                    {
                        "service.name": "spb-assistant-agent",
                        "service.version": __version__,
                    }
                ),
                sampler=ParentBased(
                    TraceIdRatioBased(settings.otel_sample_ratio)
                ),
                shutdown_on_exit=False,
                span_limits=SpanLimits(
                    max_attributes=12,
                    max_attribute_length=64,
                    max_events=0,
                    max_links=0,
                ),
            )
            session = _LocalOTLPSession()
            exporter = _ResilientExporter(
                OTLPSpanExporter(
                    endpoint=settings.otel_endpoint.strip(),
                    # Nonempty explicit headers prevent ambient OTEL headers.
                    headers={"Content-Type": "application/x-protobuf"},
                    timeout=settings.otel_export_timeout_seconds,
                    compression=Compression.NoCompression,
                    session=session,
                ),
                metrics,
            )
            provider.add_span_processor(
                BatchSpanProcessor(
                    exporter,
                    max_queue_size=256,
                    max_export_batch_size=64,
                    schedule_delay_millis=1000,
                    export_timeout_millis=int(
                        settings.otel_export_timeout_seconds * 1000
                    ),
                )
            )
            attached = True
            active = WorkflowTelemetry(
                metrics, provider.get_tracer("spb_assistant_api.workflow", "1")
            )
        except Exception:
            fallback._best_effort(
                "initialize",
                lambda: metrics.observe_telemetry_error("initialize"),
            )
            active = fallback
        yield active
    finally:
        if provider is not None:
            # SDK drains its bounded batch queue; never block the event loop.
            await asyncio.to_thread(
                fallback._best_effort, "shutdown", provider.shutdown
            )
        if exporter is not None and not attached:
            fallback._best_effort("shutdown", exporter.shutdown)
        if session is not None:
            fallback._best_effort("shutdown", session.close)
