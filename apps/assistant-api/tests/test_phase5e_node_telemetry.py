from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from pydantic import ValidationError
from requests import RequestException, Response, Session
from spb_assistant_api.adapters.checkpointer_factory import (
    create_in_memory_checkpointer,
)
from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.adapters.in_memory_receipts import (
    InMemoryToolExecutionRepository,
)
from spb_assistant_api.agent_demo import create_demo_app
from spb_assistant_api.domain.failures import AgentFailure, FailureCategory
from spb_assistant_api.domain.results import TrackingData, TrackingEvent
from spb_assistant_api.observability import telemetry as telemetry_module
from spb_assistant_api.observability.metrics import AGENT_NODES, ServiceMetrics
from spb_assistant_api.observability.telemetry import (
    WorkflowTelemetry,
    create_workflow_telemetry,
)
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.workflow.composition import create_agent_runtime
from spb_assistant_api.workflow.instrumentation import instrument_node

NOW = datetime(2026, 9, 7, tzinfo=UTC)
MAIL = "1234567890123"
SECRET = "sensitive-question-key-and-provider-body"


@pytest.fixture
def bundle():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource({"service.name": "telemetry-test"}),
        sampler=ParentBased(TraceIdRatioBased(1)),
        shutdown_on_exit=False,
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    metrics = ServiceMetrics()
    telemetry = WorkflowTelemetry(metrics, provider.get_tracer("test"))
    yield telemetry, exporter, metrics
    provider.shutdown()


def _runtime(telemetry, *, failures=(), checkpointer=None):
    gateway = FakeTrackingGateway(
        {
            MAIL: TrackingData(
                mail_no=MAIL, current_status="运输中", queried_at=NOW,
                events=[TrackingEvent(description="合成运输节点", occurred_at=NOW)],
            )
        },
        scripted_failures=list(failures),
    )
    runtime = create_agent_runtime(
        checkpointer=checkpointer or create_in_memory_checkpointer(),
        receipts=InMemoryToolExecutionRepository(),
        tracking_gateway=gateway,
        clock=lambda: NOW,
        telemetry=telemetry,
    )
    return runtime, gateway


def _roots(exporter):
    return [
        span
        for span in exporter.get_finished_spans()
        if span.name == "agent.workflow"
    ]


def _nodes(exporter, name):
    return [
        span
        for span in exporter.get_finished_spans()
        if span.name == f"agent.node.{name}"
    ]


def _sample(metrics, metric, **labels):
    return metrics.registry.get_sample_value(metric, labels) or 0


def test_real_nodes_are_children_and_logs_correlate_without_state(
    bundle, caplog
):
    telemetry, exporter, metrics = bundle
    runtime, gateway = _runtime(telemetry)
    global_provider = trace.get_tracer_provider()
    caplog.set_level(logging.INFO, logger="spb_assistant_api.workflow_trace")
    result = asyncio.run(
        runtime.start(thread_id=SECRET, message=f"查邮件 {MAIL}")
    )
    assert result["phase"] == "completed"
    assert len(gateway.commands) == 1
    spans = exporter.get_finished_spans()
    (root,) = _roots(exporter)
    assert root.parent is None
    assert root.attributes == {
        "agent.workflow.mode": "start",
        "agent.outcome": "completed",
    }
    assert len(spans) == 7
    for span in spans:
        assert span.start_time < span.end_time
        assert not span.events and not span.links
        if span is not root:
            assert span.parent.span_id == root.context.span_id
            assert span.context.trace_id == root.context.trace_id
            assert (
                root.start_time
                <= span.start_time
                < span.end_time
                <= root.end_time
            )
    encoded = "".join(span.to_json() for span in spans)
    assert MAIL not in encoded and SECRET not in encoded
    record = next(r for r in caplog.records if r.msg == "agent_workflow_trace")
    assert record.trace_id == f"{root.context.trace_id:032x}"
    assert trace.get_tracer_provider() is global_provider
    assert not trace.get_current_span().get_span_context().is_valid
    assert (
        _sample(
            metrics,
            "assistant_agent_workflow_invocations_total",
            mode="start",
            outcome="completed",
        )
        == 1
    )
    # A fresh query enters the graph and performs a fresh logical Tool call.
    assert (
        asyncio.run(runtime.start(thread_id=SECRET, message=f"查邮件 {MAIL}"))[
            "phase"
        ]
        == "completed"
    )
    assert len(gateway.commands) == 2
    assert (
        _nodes(exporter, "execute_tool")[-1].attributes["agent.tool.reused"]
        is False
    )


def test_interrupt_ends_span_and_resume_opens_new_trace(bundle):
    telemetry, exporter, _ = bundle
    runtime, _ = _runtime(telemetry)
    assert (
        asyncio.run(runtime.start(thread_id="pause", message="查邮件"))[
            "phase"
        ]
        == "waiting_user"
    )
    (paused,) = _nodes(exporter, "clarify")
    (first_root,) = _roots(exporter)
    assert paused.attributes["agent.outcome"] == "interrupted"
    assert paused.status.status_code is trace.StatusCode.UNSET
    assert paused.end_time <= first_root.end_time
    assert (
        asyncio.run(runtime.resume(thread_id="pause", message=MAIL))["phase"]
        == "completed"
    )
    roots = _roots(exporter)
    resumed = _nodes(exporter, "clarify")[1]
    assert roots[1].attributes["agent.workflow.mode"] == "resume"
    assert roots[1].parent is None
    assert roots[0].context.trace_id != roots[1].context.trace_id
    assert first_root.end_time < roots[1].start_time <= resumed.start_time
    assert resumed.attributes["agent.outcome"] == "success"
    snapshot = asyncio.run(runtime.graph.aget_state(runtime.config("pause")))
    checkpoint = json.dumps(snapshot.values, default=str)
    assert "trace_id" not in checkpoint and "span_id" not in checkpoint


def test_retry_has_two_actual_tool_spans_with_typed_failure(bundle):
    telemetry, exporter, metrics = bundle
    failure = AgentFailure(
        category=FailureCategory.UPSTREAM_TIMEOUT,
        code="tracking_timeout",
        message=SECRET,
        retryable=True,
    )
    runtime, gateway = _runtime(telemetry, failures=[failure])
    result = asyncio.run(
        runtime.start(thread_id="retry", message=f"查邮件 {MAIL}")
    )
    assert result["phase"] == "completed" and len(gateway.commands) == 2
    failed, succeeded = _nodes(exporter, "execute_tool")
    assert failed.attributes["agent.tool.attempt"] == 1
    assert failed.attributes["agent.failure.category"] == "upstream_timeout"
    assert failed.attributes["agent.outcome"] == "failed"
    assert failed.status.status_code is trace.StatusCode.ERROR
    assert succeeded.attributes["agent.tool.attempt"] == 2
    assert succeeded.attributes["agent.tool.reused"] is False
    assert len(_nodes(exporter, "recover")) == 1
    assert SECRET not in "".join(
        span.to_json() for span in exporter.get_finished_spans()
    )
    assert (
        _sample(
            metrics,
            "assistant_agent_node_executions_total",
            node="execute_tool",
            outcome="failed",
        )
        == 1
    )


@pytest.mark.parametrize("stream", [False, True])
def test_demo_lifespan_flushes_and_http_replay_does_not_create_nodes(
    tmp_path: Path, monkeypatch, stream
):
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(
        telemetry_module, "OTLPSpanExporter", lambda **kwargs: exporter
    )
    settings = AssistantSettings(
        _env_file=None, otel_enabled=True, otel_sample_ratio=1
    )
    app = create_demo_app(
        database_path=tmp_path / "agent.db", settings=settings
    )
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post(
                "/v2/agent/messages",
                headers={"Idempotency-Key": "replay"},
                json={"message": f"查邮件 {MAIL}", "stream": stream},
            )
            assert response.status_code == 200
        assert (
            _sample(
                app.state.metrics,
                "assistant_agent_node_executions_total",
                node="execute_tool",
                outcome="success",
            )
            == 1
        )
        assert (
            "trace_id" not in response.text and "span_id" not in response.text
        )
    assert len(_roots(exporter)) == 1
    assert len(_nodes(exporter, "execute_tool")) == 1
    assert (
        exporter.export(()) is SpanExportResult.FAILURE
    )  # closed by lifespan
    assert (
        _sample(
            app.state.metrics,
            "assistant_agent_trace_export_batches_total",
            outcome="success",
        )
        >= 1
    )


def test_zero_sampling_overrides_ambient_parent_but_keeps_metrics(monkeypatch):
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(
        telemetry_module, "OTLPSpanExporter", lambda **kwargs: exporter
    )
    metrics = ServiceMetrics()
    settings = AssistantSettings(
        _env_file=None, otel_enabled=True, otel_sample_ratio=0
    )

    async def scenario():
        parent = trace.NonRecordingSpan(
            trace.SpanContext(1, 2, True, trace.TraceFlags(1))
        )
        with trace.use_span(parent):
            async with create_workflow_telemetry(
                settings, metrics=metrics
            ) as telemetry:
                runtime, _ = _runtime(telemetry)
                await runtime.start(thread_id="zero", message=f"查邮件 {MAIL}")
            assert trace.get_current_span() is parent

    asyncio.run(scenario())
    assert exporter.get_finished_spans() == ()
    assert (
        _sample(
            metrics,
            "assistant_agent_node_executions_total",
            node="execute_tool",
            outcome="success",
        )
        == 1
    )


def test_disabled_telemetry_does_not_construct_exporter(monkeypatch):
    def forbidden(**kwargs):
        raise AssertionError("exporter must not be created")

    monkeypatch.setattr(telemetry_module, "OTLPSpanExporter", forbidden)
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "https://untrusted.example"
    )
    metrics = ServiceMetrics()

    async def scenario():
        async with create_workflow_telemetry(
            AssistantSettings(_env_file=None), metrics=metrics
        ) as telemetry:
            runtime, _ = _runtime(telemetry)
            assert (
                await runtime.start(thread_id="disabled", message="查邮件")
            )["phase"] == "waiting_user"

    asyncio.run(scenario())
    assert (
        _sample(
            metrics,
            "assistant_agent_node_executions_total",
            node="clarify",
            outcome="interrupted",
        )
        == 1
    )


@pytest.mark.parametrize("async_node", [False, True])
def test_unhandled_node_exception_propagates_without_recording_text(
    bundle, async_node
):
    telemetry, exporter, _ = bundle

    def broken(state):
        raise ValueError(SECRET)

    async def async_broken(state):
        return broken(state)

    node = instrument_node(
        "understand", async_broken if async_node else broken, telemetry
    )
    with pytest.raises(ValueError, match=SECRET):
        if async_node:
            asyncio.run(node({}))
        else:
            node({})
    (span,) = exporter.get_finished_spans()
    assert span.attributes["agent.outcome"] == "error"
    assert span.status.status_code is trace.StatusCode.ERROR
    assert span.status.description is None and not span.events
    assert SECRET not in span.to_json()


def test_cancellation_closes_node_and_workflow_and_is_not_swallowed(bundle):
    telemetry, exporter, metrics = bundle

    async def scenario():
        entered = asyncio.Event()

        async def slow(state):
            entered.set()
            await asyncio.Event().wait()

        async def operation():
            with telemetry.measure():
                await instrument_node("understand", slow, telemetry)({})

        task = asyncio.create_task(operation())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(exporter.get_finished_spans()) == 2
    assert all(
        s.attributes["agent.outcome"] == "cancelled"
        for s in exporter.get_finished_spans()
    )
    assert (
        _sample(
            metrics,
            "assistant_agent_node_executions_total",
            node="understand",
            outcome="cancelled",
        )
        == 1
    )


@pytest.mark.parametrize("operation", ["initialize", "export", "shutdown"])
def test_exporter_failures_do_not_break_business_or_leak_errors(
    monkeypatch, caplog, operation
):
    class BrokenExporter(InMemorySpanExporter):
        def export(self, spans):
            if operation == "export":
                raise RuntimeError(SECRET)
            return super().export(spans)

        def shutdown(self):
            if operation == "shutdown":
                raise RuntimeError(SECRET)
            super().shutdown()

    def factory(**kwargs):
        if operation == "initialize":
            raise RuntimeError(SECRET)
        return BrokenExporter()

    monkeypatch.setattr(telemetry_module, "OTLPSpanExporter", factory)
    metrics = ServiceMetrics()

    async def scenario():
        async with create_workflow_telemetry(
            AssistantSettings(
                _env_file=None, otel_enabled=True, otel_sample_ratio=1
            ),
            metrics=metrics,
        ) as telemetry:
            runtime, _ = _runtime(telemetry)
            assert (
                await runtime.start(thread_id="safe", message=f"查邮件 {MAIL}")
            )["phase"] == "completed"

    asyncio.run(scenario())
    assert (
        _sample(
            metrics,
            "assistant_agent_telemetry_errors_total",
            operation=operation,
        )
        >= 1
    )
    assert SECRET not in caplog.text


def test_broken_tracer_and_metrics_do_not_change_node_result():
    class BrokenTracer:
        def start_span(self, *args, **kwargs):
            raise RuntimeError(SECRET)

    metrics = ServiceMetrics()
    telemetry = WorkflowTelemetry(metrics, BrokenTracer())
    assert instrument_node(
        "understand", lambda state: {"ok": True}, telemetry
    )({}) == {"ok": True}
    assert (
        _sample(
            metrics,
            "assistant_agent_telemetry_errors_total",
            operation="start",
        )
        == 1
    )

    class BrokenMetrics:
        def observe_agent_node(self, **kwargs):
            raise RuntimeError(SECRET)

        def observe_telemetry_error(self, operation):
            raise RuntimeError(SECRET)

    assert (
        instrument_node(
            "ingest", lambda state: {}, WorkflowTelemetry(BrokenMetrics())
        )({})
        == {}
    )


def test_labels_and_attributes_are_allowlisted(bundle):
    telemetry, exporter, metrics = bundle
    with telemetry.measure(node=SECRET) as observation:
        observation.outcome = SECRET
        observation.failure_category = SECRET
        observation.tool_attempt = 1000000
    (span,) = exporter.get_finished_spans()
    assert span.name == "agent.node.unknown"
    assert span.attributes["agent.tool.attempt"] == 8
    assert "agent.failure.category" not in span.attributes
    assert SECRET not in metrics.render().decode() + span.to_json()
    assert (
        _sample(
            metrics,
            "assistant_agent_node_executions_total",
            node="unknown",
            outcome="unknown",
        )
        == 1
    )
    assert len(AGENT_NODES) == 8


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://user:pass@localhost:4318/v1/traces",
        "file:///v1/traces",
        "https://localhost:4318/v1/traces?token=secret",
        "http://localhost:4318",
        "http://localhost:4318/v1/traces#secret",
    ],
)
def test_otel_settings_reject_unsafe_endpoints(endpoint):
    with pytest.raises(ValidationError):
        AssistantSettings(
            _env_file=None, otel_enabled=True, otel_endpoint=endpoint
        )


@pytest.mark.parametrize("ratio", [-0.1, 1.1])
def test_otel_settings_reject_invalid_sampling(ratio):
    with pytest.raises(ValidationError):
        AssistantSettings(_env_file=None, otel_sample_ratio=ratio)


@pytest.mark.parametrize("status", [200, 302, 401, 503])
def test_export_transport_strips_body_reason_and_disables_redirects(
    monkeypatch, status
):
    received = {}

    def post(self, url, **kwargs):
        received.update(kwargs)
        response = Response()
        response.status_code = status
        response.reason = SECRET
        response._content = SECRET.encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(Session, "post", post)
    session = telemetry_module._LocalOTLPSession()
    result = session.post(
        "http://localhost:4318/v1/traces",
        data=b"test",
        timeout=1,
        cert="ambient-secret",
    )
    assert session.trust_env is False
    assert result.status_code == (400 if status == 302 else status)
    assert result.content == b"" and SECRET not in result.reason
    assert received["allow_redirects"] is False and received["stream"] is True
    assert "cert" not in received
    session.close()


def test_export_transport_redacts_connection_errors(monkeypatch):
    def post(*args, **kwargs):
        raise RequestException(SECRET)

    monkeypatch.setattr(Session, "post", post)
    with pytest.raises(RequestException, match="^telemetry_transport_error$"):
        telemetry_module._LocalOTLPSession().post(
            "http://localhost:4318/v1/traces", timeout=1
        )


def test_concurrent_graph_invocations_do_not_share_trace_parents(bundle):
    telemetry, exporter, _ = bundle
    runtime, _ = _runtime(telemetry)

    async def scenario():
        return await asyncio.gather(
            *(
                runtime.start(
                    thread_id=f"parallel-{i}", message=f"查邮件 {MAIL}"
                )
                for i in range(3)
            )
        )

    assert all(
        result["phase"] == "completed" for result in asyncio.run(scenario())
    )
    roots = _roots(exporter)
    assert len({root.context.trace_id for root in roots}) == 3
    for root in roots:
        children = [
            span
            for span in exporter.get_finished_spans()
            if span.parent and span.parent.span_id == root.context.span_id
        ]
        assert len(children) == 6
        assert all(
            span.context.trace_id == root.context.trace_id for span in children
        )


def test_explicit_sdk_config_ignores_ambient_headers_and_resources(
    monkeypatch,
):
    exporter = InMemorySpanExporter()
    received = {}

    def factory(**kwargs):
        received.update(kwargs)
        return exporter

    monkeypatch.setattr(telemetry_module, "OTLPSpanExporter", factory)
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", f"private={SECRET}")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"Authorization={SECRET}")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    settings = AssistantSettings(
        _env_file=None, otel_enabled=True, otel_sample_ratio=1
    )

    async def scenario():
        async with create_workflow_telemetry(
            settings, metrics=ServiceMetrics()
        ) as telemetry:
            with telemetry.measure(node="ingest"):
                pass

    asyncio.run(scenario())
    assert received["endpoint"] == settings.otel_endpoint
    assert received["headers"] == {"Content-Type": "application/x-protobuf"}
    (span,) = exporter.get_finished_spans()
    assert set(span.resource.attributes) == {"service.name", "service.version"}
    assert SECRET not in span.to_json()


def test_invalid_observation_never_changes_business_result(bundle):
    telemetry, _, _ = bundle
    update = {"tool_calls": [{"status": {"not": "a status"}}]}
    assert (
        instrument_node("execute_tool", lambda state: update, telemetry)({})
        is update
    )
