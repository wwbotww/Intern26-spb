from __future__ import annotations

import asyncio
import json

import pytest
from langgraph.errors import GraphRecursionError

from spb_assistant_api.adapters.checkpointer_factory import (
    create_in_memory_checkpointer,
)
from spb_assistant_api.domain.agent_actions import ClarificationRequest
from spb_assistant_api.workflow import (
    AgentWorkflowRuntime,
    build_spike_graph,
)


def _runtime(*, recursion_limit: int = 8) -> AgentWorkflowRuntime:
    return AgentWorkflowRuntime(
        build_spike_graph(checkpointer=create_in_memory_checkpointer()),
        recursion_limit=recursion_limit,
    )


def test_spike_completes_direct_path_with_json_native_checkpoint() -> None:
    runtime = _runtime()

    result = asyncio.run(
        runtime.start(
            thread_id="direct-path",
            message="查询邮件 1234567890123",
        )
    )

    assert result == {
        "phase": "completed",
        "reply": "阶段 0 LangGraph Spike 已完成参数收集。",
        "finish_reason": "stop",
    }
    snapshot = runtime.graph.get_state(runtime.config("direct-path"))
    assert snapshot.next == ()
    assert snapshot.values["audit_events"] == [
        "query_understood",
        "spike_completed",
    ]
    json.dumps(snapshot.values, ensure_ascii=False)


def test_spike_interrupts_and_resumes_on_the_same_thread() -> None:
    runtime = _runtime()

    interrupted = asyncio.run(
        runtime.start(
            thread_id="clarification-path",
            message="帮我查一下邮件",
        )
    )

    interrupts = interrupted["__interrupt__"]
    assert len(interrupts) == 1
    request = ClarificationRequest.model_validate(interrupts[0].value)
    assert request.intent == "tracking"
    assert [item.name for item in request.required_inputs] == ["mail_no"]

    paused = runtime.graph.get_state(runtime.config("clarification-path"))
    assert paused.next == ("clarify",)
    assert paused.values["audit_events"] == ["clarification_required"]

    completed = asyncio.run(
        runtime.resume_tracking(
            thread_id="clarification-path",
            mail_no="1234567890123",
        )
    )

    assert completed["phase"] == "completed"
    snapshot = runtime.graph.get_state(
        runtime.config("clarification-path")
    )
    assert snapshot.values["audit_events"] == [
        "clarification_required",
        "clarification_resumed",
        "query_understood",
        "spike_completed",
    ]
    assert snapshot.values["mail_no"] == "1234567890123"


def test_spike_checkpoints_are_isolated_by_thread() -> None:
    runtime = _runtime()

    asyncio.run(
        runtime.start(
            thread_id="waiting-thread",
            message="帮我查一下邮件",
        )
    )
    asyncio.run(
        runtime.start(
            thread_id="completed-thread",
            message="查询 9876543210123",
        )
    )

    waiting = runtime.graph.get_state(runtime.config("waiting-thread"))
    completed = runtime.graph.get_state(
        runtime.config("completed-thread")
    )
    assert waiting.values["phase"] == "clarifying"
    assert waiting.next == ("clarify",)
    assert completed.values["phase"] == "completed"
    assert completed.next == ()


def test_spike_emits_async_langgraph_events() -> None:
    runtime = _runtime()

    async def collect_events() -> list[dict[str, object]]:
        return [
            dict(event)
            async for event in runtime.stream_events(
                thread_id="event-thread",
                message="查询 1234567890123",
            )
        ]

    events = asyncio.run(collect_events())

    event_names = {str(event.get("event")) for event in events}
    node_names = {str(event.get("name")) for event in events}
    assert "on_chain_start" in event_names
    assert "on_chain_end" in event_names
    assert {"understand", "complete"}.issubset(node_names)


def test_spike_uses_explicit_recursion_limit_as_last_resort() -> None:
    runtime = _runtime(recursion_limit=1)

    with pytest.raises(GraphRecursionError):
        asyncio.run(
            runtime.start(
                thread_id="bounded-thread",
                message="查询 1234567890123",
            )
        )


@pytest.mark.parametrize("thread_id", ["", "   ", "x" * 256])
def test_runtime_rejects_invalid_thread_id(thread_id: str) -> None:
    runtime = _runtime()

    with pytest.raises(ValueError, match="thread_id"):
        runtime.config(thread_id)
