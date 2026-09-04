from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest

from spb_assistant_api.adapters.checkpointer_factory import (
    create_in_memory_checkpointer,
)
from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.adapters.in_memory_receipts import (
    InMemoryToolExecutionRepository,
)
from spb_assistant_api.domain.failures import AgentFailure, FailureCategory
from spb_assistant_api.domain.results import TrackingData
from spb_assistant_api.observability.agent_trace import (
    log_agent_workflow_trace,
)
from spb_assistant_api.workflow.composition import create_agent_runtime
from spb_assistant_api.workflow.tracing import (
    AgentWorkflowTrace,
    build_agent_workflow_trace,
)


NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
MAIL_NO = "1234567890123"


def _tracking_data(*, mail_no: str = MAIL_NO) -> TrackingData:
    return TrackingData(
        mail_no=mail_no,
        current_status="运输中",
        queried_at=NOW,
    )


def _timeout() -> AgentFailure:
    return AgentFailure(
        category=FailureCategory.UPSTREAM_TIMEOUT,
        code="tracking_timeout",
        message="provider-sensitive-timeout",
        retryable=True,
    )


def _runtime(
    traces: list[AgentWorkflowTrace],
    *,
    records: dict[str, TrackingData] | None = None,
    failures: list[AgentFailure] | None = None,
    max_steps: int = 8,
):
    gateway = FakeTrackingGateway(
        records,
        scripted_failures=failures or [],
    )
    runtime = create_agent_runtime(
        checkpointer=create_in_memory_checkpointer(),
        receipts=InMemoryToolExecutionRepository(),
        tracking_gateway=gateway,
        max_steps=max_steps,
        clock=lambda: NOW,
        workflow_trace_sink=traces.append,
    )
    return runtime, gateway


def test_workflow_trace_distinguishes_interrupt_resume_and_checkpoint() -> None:
    traces: list[AgentWorkflowTrace] = []
    runtime, gateway = _runtime(
        traces,
        records={MAIL_NO: _tracking_data()},
    )

    waiting = asyncio.run(
        runtime.start(thread_id="trace-interrupt", message="帮我查邮件")
    )
    completed = asyncio.run(
        runtime.resume(
            thread_id="trace-interrupt",
            message=MAIL_NO,
        )
    )

    assert waiting["phase"] == "waiting_user"
    assert completed["phase"] == "completed"
    assert len(gateway.commands) == 1
    assert len(traces) == 2

    interrupted, resumed = traces
    assert interrupted.interrupted is True
    assert interrupted.resumed is False
    assert interrupted.checkpoint_before is False
    assert interrupted.checkpoint_after is True
    assert interrupted.event_window == "delta"
    assert interrupted.node_path == (
        "ingest",
        "understand",
        "decide_next",
        "clarify",
    )
    assert interrupted.edge_path[-1] == "clarify->__interrupt__"

    assert resumed.interrupted is False
    assert resumed.resumed is True
    assert resumed.checkpoint_before is True
    assert resumed.checkpoint_after is True
    assert resumed.event_window == "delta"
    assert resumed.node_path[0] == "clarify"
    assert resumed.node_path[-1] == "compose_response"
    assert resumed.edge_path[-1] == "compose_response->__end__"
    assert all(
        step.event != "conversation_started" for step in resumed.steps
    )


def test_workflow_trace_explains_retry_without_logging_business_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    traces: list[AgentWorkflowTrace] = []
    runtime, gateway = _runtime(
        traces,
        records={MAIL_NO: _tracking_data()},
        failures=[_timeout()],
    )

    result = asyncio.run(
        runtime.start(
            thread_id="trace-retry",
            message=f"查邮件 {MAIL_NO}",
        )
    )

    assert result["phase"] == "completed"
    assert len(gateway.commands) == 2
    trace = traces[0]
    assert trace.retry_count == 1
    assert trace.logical_tool_call_count == 1
    assert trace.node_path.count("execute_tool") == 2
    assert [step.event for step in trace.steps].count(
        "tool_call_failed"
    ) == 1
    assert [step.event for step in trace.steps].count(
        "tool_call_succeeded"
    ) == 1
    assert "recover->decide_next" in trace.edge_path

    caplog.set_level(
        logging.INFO,
        logger="spb_assistant_api.workflow_trace",
    )
    log_agent_workflow_trace(trace)
    record = caplog.records[-1]
    assert record.trace_type == "agent_workflow"
    assert record.conversation_ref.startswith("sha256:")
    assert record.retry_count == 1
    encoded = json.dumps(record.__dict__, ensure_ascii=False, default=str)
    assert MAIL_NO not in encoded
    assert "查邮件" not in encoded
    assert "provider-sensitive-timeout" not in encoded
    assert "current_status" not in encoded


@pytest.mark.parametrize(
    ("failures", "records", "max_steps", "category", "retry_count"),
    [
        (
            [_timeout(), _timeout()],
            None,
            8,
            "upstream_timeout",
            1,
        ),
        (
            [],
            {MAIL_NO: _tracking_data(mail_no="9999999999999")},
            8,
            "contract_violation",
            0,
        ),
        (
            [_timeout()],
            {MAIL_NO: _tracking_data()},
            1,
            "loop_budget_exceeded",
            1,
        ),
    ],
)
def test_fault_outcomes_are_visible_in_the_sanitized_trace(
    failures: list[AgentFailure],
    records: dict[str, TrackingData] | None,
    max_steps: int,
    category: str,
    retry_count: int,
) -> None:
    traces: list[AgentWorkflowTrace] = []
    runtime, _ = _runtime(
        traces,
        records=records,
        failures=failures,
        max_steps=max_steps,
    )

    result = asyncio.run(
        runtime.start(
            thread_id=f"trace-{category}",
            message=f"查邮件 {MAIL_NO}",
        )
    )

    assert result["phase"] == "failed"
    assert result["failure"]["category"] == category
    assert traces[0].outcome == "failed"
    assert traces[0].failure_category == category
    assert traces[0].retry_count == retry_count


def test_trace_projection_drops_unknown_details_and_bounds_codes() -> None:
    trace = build_agent_workflow_trace(
        before_state={},
        after_state={
            "conversation_id": "conversation-secret",
            "turn_id": "turn-secret",
            "phase": "completed",
            "audit_events": [
                {
                    "event_type": "query_understood",
                    "node": "understand",
                    "phase": "ready",
                    "details": {
                        "intent": "tracking",
                        "prompt_version": "unsafe user content with spaces",
                        "raw_message": "must never pass",
                    },
                }
            ],
        },
        resumed=False,
        checkpoint_before=False,
        checkpoint_after=True,
    )

    assert trace.steps[0].details == (
        ("intent", "tracking"),
        ("prompt_version", "unclassified"),
    )
    assert "raw_message" not in str(trace.steps[0].details)

    errored = build_agent_workflow_trace(
        before_state={},
        after_state={
            "phase": "understanding",
            "audit_events": [
                {
                    "event_type": "user_message_received",
                    "node": "ingest",
                    "phase": "understanding",
                    "details": {},
                }
            ],
        },
        resumed=False,
        checkpoint_before=False,
        checkpoint_after=True,
        outcome_override="error",
    )
    assert errored.edge_path[-1] == "ingest->__error__"


def test_trace_sink_failure_never_changes_workflow_result() -> None:
    gateway = FakeTrackingGateway({MAIL_NO: _tracking_data()})

    def failing_sink(_: AgentWorkflowTrace) -> None:
        raise RuntimeError("telemetry unavailable")

    runtime = create_agent_runtime(
        checkpointer=create_in_memory_checkpointer(),
        receipts=InMemoryToolExecutionRepository(),
        tracking_gateway=gateway,
        clock=lambda: NOW,
        workflow_trace_sink=failing_sink,
    )

    result = asyncio.run(
        runtime.start(
            thread_id="trace-sink-failure",
            message=f"查邮件 {MAIL_NO}",
        )
    )

    assert result["phase"] == "completed"
    assert len(gateway.commands) == 1
