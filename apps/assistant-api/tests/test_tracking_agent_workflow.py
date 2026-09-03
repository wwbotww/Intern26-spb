from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from spb_assistant_api.adapters.checkpointer_factory import (
    create_in_memory_checkpointer,
)
from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.adapters.in_memory_receipts import (
    InMemoryToolExecutionRepository,
)
from spb_assistant_api.domain.agent_actions import ClarificationRequest
from spb_assistant_api.domain.failures import AgentFailure, FailureCategory
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.domain.results import TrackingData, TrackingEvent
from spb_assistant_api.services.agent_tools import (
    AgentCommandDispatcher,
    AgentToolRegistry,
    ToolExecutor,
)
from spb_assistant_api.services.query_understanding import (
    RuleBasedQueryUnderstander,
)
from spb_assistant_api.services.result_validator import AgentResultValidator
from spb_assistant_api.tools.tracking import TrackingTool
from spb_assistant_api.workflow import (
    TrackingAgentGraphDependencies,
    TrackingAgentRuntime,
    build_tracking_agent_graph,
    create_tracking_agent_runtime,
)
from spb_assistant_api.workflow.policy import WorkflowPolicy


NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)
MAIL_NO = "1234567890123"


@dataclass(frozen=True)
class Kernel:
    runtime: TrackingAgentRuntime
    gateway: FakeTrackingGateway
    receipts: InMemoryToolExecutionRepository


def _tracking_data(
    *,
    mail_no: str = MAIL_NO,
) -> TrackingData:
    return TrackingData(
        mail_no=mail_no,
        current_status="运输中",
        events=[
            TrackingEvent(
                event_code="accepted",
                description="邮件已收寄",
                occurred_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
                location="北京市",
            ),
            TrackingEvent(
                event_code="transit",
                description="邮件运输中",
                occurred_at=datetime(2026, 9, 3, 7, tzinfo=UTC),
                location="天津市",
            ),
        ],
        queried_at=NOW,
    )


def _kernel(
    *,
    records: dict[str, TrackingData] | None = None,
    failures: list[AgentFailure] | None = None,
    max_steps: int = 8,
    max_retries: int = 1,
    clock: Callable[[], datetime] | None = None,
) -> Kernel:
    resolved_clock = clock or (lambda: NOW)
    gateway = FakeTrackingGateway(
        records,
        scripted_failures=failures or [],
    )
    registry = AgentToolRegistry(
        [TrackingTool(gateway)],
        required_intents=frozenset({Intent.TRACKING}),
    )
    policy = WorkflowPolicy(registry.descriptors)
    receipts = InMemoryToolExecutionRepository()
    executor = ToolExecutor(
        AgentCommandDispatcher(registry),
        receipts,
        clock=resolved_clock,
    )
    graph = build_tracking_agent_graph(
        checkpointer=create_in_memory_checkpointer(),
        dependencies=TrackingAgentGraphDependencies(
            understander=RuleBasedQueryUnderstander(),
            policy=policy,
            executor=executor,
            validator=AgentResultValidator(),
        ),
    )
    return Kernel(
        runtime=TrackingAgentRuntime(
            graph,
            max_steps=max_steps,
            max_retries=max_retries,
            clock=resolved_clock,
        ),
        gateway=gateway,
        receipts=receipts,
    )


def _timeout() -> AgentFailure:
    return AgentFailure(
        category=FailureCategory.UPSTREAM_TIMEOUT,
        code="tracking_timeout",
        message="tracking timeout",
        retryable=True,
    )


def test_tracking_agent_completes_direct_fake_tool_path() -> None:
    kernel = _kernel(records={MAIL_NO: _tracking_data()})

    result = asyncio.run(
        kernel.runtime.start(
            thread_id="direct-agent",
            message=f"查询邮件 {MAIL_NO}",
        )
    )

    assert result["phase"] == "completed"
    assert result["active_intent"] == "tracking"
    assert result["result"]["status"] == "success"
    assert result["result"]["data"]["mail_no"] == MAIL_NO
    assert result["failure"] is None
    assert len(kernel.gateway.commands) == 1
    assert len(kernel.receipts) == 1

    snapshot = kernel.runtime.graph.get_state(
        kernel.runtime.config("direct-agent")
    )
    json.dumps(snapshot.values, ensure_ascii=False)
    assert snapshot.values["tool_call_count"] == 1
    assert snapshot.values["retry_count"] == 0
    event_types = [
        event["event_type"] for event in snapshot.values["audit_events"]
    ]
    assert MAIL_NO not in json.dumps(
        snapshot.values["audit_events"],
        ensure_ascii=False,
    )
    assert event_types == [
        "conversation_started",
        "user_message_received",
        "query_understood",
        "action_decided",
        "tool_call_started",
        "tool_call_succeeded",
        "result_validated",
        "response_prepared",
    ]


def test_tracking_agent_interrupts_and_resumes_same_thread() -> None:
    kernel = _kernel(records={MAIL_NO: _tracking_data()})

    waiting = asyncio.run(
        kernel.runtime.start(
            thread_id="resume-agent",
            message="帮我查一下邮件",
        )
    )

    assert waiting["phase"] == "waiting_user"
    assert waiting["reply"] == "请提供邮件号。"
    request = ClarificationRequest.model_validate(
        waiting["__interrupt__"][0].value
    )
    assert request.prompt == "请提供邮件号。"
    assert [item.name for item in request.required_inputs] == ["mail_no"]
    first_turn_id = waiting["turn_id"]

    completed = asyncio.run(
        kernel.runtime.resume_tracking(
            thread_id="resume-agent",
            mail_no=MAIL_NO,
        )
    )

    assert completed["phase"] == "completed"
    assert completed["turn_id"] != first_turn_id
    assert len(kernel.gateway.commands) == 1
    snapshot = kernel.runtime.graph.get_state(
        kernel.runtime.config("resume-agent")
    )
    assert snapshot.values["turn_count"] == 2
    assert snapshot.values["step_count"] == 1
    event_types = {
        event["event_type"]
        for event in snapshot.values["audit_events"]
    }
    assert "clarification_requested" in event_types
    assert "clarification_resumed" in event_types


def test_resume_refreshes_deadline_after_user_wait() -> None:
    current = [NOW]
    kernel = _kernel(
        records={MAIL_NO: _tracking_data()},
        clock=lambda: current[0],
    )
    asyncio.run(
        kernel.runtime.start(
            thread_id="delayed-resume-agent",
            message="帮我查一下邮件",
        )
    )
    before = kernel.runtime.graph.get_state(
        kernel.runtime.config("delayed-resume-agent")
    )
    old_deadline = datetime.fromisoformat(before.values["deadline_at"])
    current[0] = NOW + timedelta(minutes=5)

    result = asyncio.run(
        kernel.runtime.resume_tracking(
            thread_id="delayed-resume-agent",
            mail_no=MAIL_NO,
        )
    )

    after = kernel.runtime.graph.get_state(
        kernel.runtime.config("delayed-resume-agent")
    )
    assert result["phase"] == "completed"
    assert datetime.fromisoformat(after.values["deadline_at"]) > old_deadline


def test_unknown_or_unavailable_intent_never_calls_a_tool() -> None:
    kernel = _kernel(records={MAIL_NO: _tracking_data()})

    unknown = asyncio.run(
        kernel.runtime.start(
            thread_id="unknown-agent",
            message="你好",
        )
    )
    unavailable = asyncio.run(
        kernel.runtime.start(
            thread_id="unavailable-agent",
            message="查一下政策",
            explicit_intent=Intent.POLICY,
        )
    )

    assert unknown["phase"] == "handoff"
    assert unavailable["phase"] == "handoff"
    assert kernel.gateway.commands == []


def test_tracking_agent_returns_typed_no_match_without_retry() -> None:
    kernel = _kernel()

    result = asyncio.run(
        kernel.runtime.start(
            thread_id="no-match-agent",
            message=f"查邮件 {MAIL_NO}",
        )
    )

    assert result["phase"] == "completed"
    assert result["result"]["status"] == "no_match"
    assert result["result"]["data"] is None
    assert len(kernel.gateway.commands) == 1


def test_transient_failure_retries_once_and_completes() -> None:
    kernel = _kernel(
        records={MAIL_NO: _tracking_data()},
        failures=[_timeout()],
    )

    result = asyncio.run(
        kernel.runtime.start(
            thread_id="retry-agent",
            message=f"查邮件 {MAIL_NO}",
        )
    )

    assert result["phase"] == "completed"
    assert len(kernel.gateway.commands) == 2
    snapshot = kernel.runtime.graph.get_state(
        kernel.runtime.config("retry-agent")
    )
    assert snapshot.values["retry_count"] == 1
    assert snapshot.values["tool_call_count"] == 1


def test_retry_exhaustion_has_deterministic_failed_result() -> None:
    kernel = _kernel(failures=[_timeout(), _timeout()])

    result = asyncio.run(
        kernel.runtime.start(
            thread_id="retry-exhausted-agent",
            message=f"查邮件 {MAIL_NO}",
        )
    )

    assert result["phase"] == "failed"
    assert result["finish_reason"] == "failed"
    assert result["failure"]["category"] == "upstream_timeout"
    assert len(kernel.gateway.commands) == 2


def test_result_contract_failure_is_not_retried_or_displayed() -> None:
    kernel = _kernel(
        records={MAIL_NO: _tracking_data(mail_no="9999999999999")}
    )

    result = asyncio.run(
        kernel.runtime.start(
            thread_id="contract-agent",
            message=f"查邮件 {MAIL_NO}",
        )
    )

    assert result["phase"] == "failed"
    assert result["result"] is None
    assert result["failure"]["category"] == "contract_violation"
    assert len(kernel.gateway.commands) == 1


def test_business_step_budget_stops_retry_before_second_call() -> None:
    kernel = _kernel(
        records={MAIL_NO: _tracking_data()},
        failures=[_timeout()],
        max_steps=1,
    )

    result = asyncio.run(
        kernel.runtime.start(
            thread_id="step-budget-agent",
            message=f"查邮件 {MAIL_NO}",
        )
    )

    assert result["phase"] == "failed"
    assert result["failure"]["category"] == "loop_budget_exceeded"
    assert len(kernel.gateway.commands) == 1


def test_checkpoint_replay_reuses_receipt_without_second_gateway_call() -> None:
    kernel = _kernel(records={MAIL_NO: _tracking_data()})
    runtime = kernel.runtime

    asyncio.run(
        runtime.start(
            thread_id="replay-agent",
            message=f"查邮件 {MAIL_NO}",
        )
    )
    history = list(
        runtime.graph.get_state_history(runtime.config("replay-agent"))
    )
    before_execute = next(
        snapshot
        for snapshot in history
        if snapshot.next == ("execute_tool",)
    )

    replayed = asyncio.run(
        runtime.graph.ainvoke(None, config=before_execute.config)
    )

    assert replayed["phase"] == "completed"
    assert len(kernel.gateway.commands) == 1
    assert len(kernel.receipts) == 1
    replay_history = list(
        runtime.graph.get_state_history(runtime.config("replay-agent"))
    )
    assert any(
        record["status"] == "reused"
        for snapshot in replay_history
        for record in snapshot.values.get("tool_calls", [])
    )


def test_tracking_agent_graph_has_expected_nodes_and_no_orphans() -> None:
    graph = _kernel().runtime.graph.get_graph()
    expected = {
        "__start__",
        "ingest",
        "understand",
        "decide_next",
        "clarify",
        "execute_tool",
        "validate_result",
        "recover",
        "compose_response",
        "__end__",
    }

    assert set(graph.nodes) == expected
    connected = {
        edge.source for edge in graph.edges
    } | {edge.target for edge in graph.edges}
    assert connected == expected


def test_tracking_agent_runtime_streams_formal_graph_events() -> None:
    kernel = _kernel(records={MAIL_NO: _tracking_data()})

    async def collect() -> list[dict[str, object]]:
        return [
            dict(event)
            async for event in kernel.runtime.stream_events(
                thread_id="stream-agent",
                message=f"查询邮件 {MAIL_NO}",
            )
        ]

    events = asyncio.run(collect())
    node_names = {str(event.get("name")) for event in events}

    assert {
        "ingest",
        "understand",
        "decide_next",
        "execute_tool",
        "validate_result",
        "compose_response",
    }.issubset(node_names)


def test_tracking_agent_checkpoints_are_isolated_by_thread() -> None:
    kernel = _kernel(records={MAIL_NO: _tracking_data()})

    asyncio.run(
        kernel.runtime.start(
            thread_id="waiting-agent-thread",
            message="帮我查邮件",
        )
    )
    asyncio.run(
        kernel.runtime.start(
            thread_id="completed-agent-thread",
            message=f"查询 {MAIL_NO}",
        )
    )

    waiting = kernel.runtime.graph.get_state(
        kernel.runtime.config("waiting-agent-thread")
    )
    completed = kernel.runtime.graph.get_state(
        kernel.runtime.config("completed-agent-thread")
    )
    assert waiting.values["phase"] == "waiting_user"
    assert waiting.next == ("clarify",)
    assert completed.values["phase"] == "completed"
    assert completed.next == ()


def test_composition_root_builds_network_free_tracking_runtime() -> None:
    gateway = FakeTrackingGateway({MAIL_NO: _tracking_data()})
    receipts = InMemoryToolExecutionRepository()
    runtime = create_tracking_agent_runtime(
        checkpointer=create_in_memory_checkpointer(),
        gateway=gateway,
        receipts=receipts,
    )

    result = asyncio.run(
        runtime.start(
            thread_id="composition-agent",
            message=f"查询邮件 {MAIL_NO}",
        )
    )

    assert result["phase"] == "completed"
    assert len(gateway.commands) == 1
    assert len(receipts) == 1
