from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from spb_assistant_api.adapters.checkpointer_factory import (
    create_in_memory_checkpointer,
)
from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.adapters.in_memory_receipts import (
    InMemoryToolExecutionRepository,
)
from spb_assistant_api.domain.agent_actions import (
    IntentClarificationRequest,
)
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.domain.results import TrackingData
from spb_assistant_api.services.agent_tools import (
    AgentCommandDispatcher,
    AgentToolRegistry,
    ToolExecutor,
)
from spb_assistant_api.services.query_understanding import (
    HybridQueryUnderstander,
)
from spb_assistant_api.services.result_validator import AgentResultValidator
from spb_assistant_api.tools.tracking import TrackingTool
from spb_assistant_api.workflow.graph import (
    TrackingAgentGraphDependencies,
    build_tracking_agent_graph,
)
from spb_assistant_api.workflow.policy import WorkflowPolicy
from spb_assistant_api.workflow.runtime import TrackingAgentRuntime


NOW = datetime(2026, 9, 3, tzinfo=UTC)
MAIL_NO = "1234567890123"


def _runtime() -> tuple[TrackingAgentRuntime, FakeTrackingGateway]:
    gateway = FakeTrackingGateway(
        {
            MAIL_NO: TrackingData(
                mail_no=MAIL_NO,
                current_status="运输中",
                queried_at=NOW,
            )
        }
    )
    registry = AgentToolRegistry(
        [TrackingTool(gateway)],
        required_intents=frozenset({Intent.TRACKING}),
    )
    graph = build_tracking_agent_graph(
        checkpointer=create_in_memory_checkpointer(),
        dependencies=TrackingAgentGraphDependencies(
            understander=HybridQueryUnderstander(),
            policy=WorkflowPolicy(registry.descriptors),
            executor=ToolExecutor(
                AgentCommandDispatcher(registry),
                InMemoryToolExecutionRepository(),
                clock=lambda: NOW,
            ),
            validator=AgentResultValidator(),
        ),
    )
    return TrackingAgentRuntime(graph, clock=lambda: NOW), gateway


def test_multi_intent_interrupt_requires_choice_before_tool_call() -> None:
    runtime, gateway = _runtime()
    thread_id = "phase2-multi"

    waiting = asyncio.run(
        runtime.start(
            thread_id=thread_id,
            message=(
                "查邮件 1234567890123，同时算北京到上海 2 公斤邮费"
            ),
        )
    )

    assert waiting["phase"] == "waiting_user"
    request = IntentClarificationRequest.model_validate(
        waiting["__interrupt__"][0].value
    )
    assert set(request.candidates) >= {Intent.TRACKING, Intent.POSTAGE}
    assert gateway.commands == []

    completed = asyncio.run(
        runtime.resume(
            thread_id=thread_id,
            selected_intent=Intent.TRACKING,
        )
    )

    assert completed["phase"] == "completed"
    assert completed["active_intent"] == "tracking"
    assert len(gateway.commands) == 1


def test_active_workflow_switch_requires_confirmation() -> None:
    runtime, gateway = _runtime()
    thread_id = "phase2-switch"
    asyncio.run(
        runtime.start(
            thread_id=thread_id,
            message="帮我查一下邮件",
        )
    )

    switch = asyncio.run(
        runtime.resume(
            thread_id=thread_id,
            message="改成查询北京到上海 2 公斤邮费",
        )
    )

    assert switch["phase"] == "waiting_user"
    request = IntentClarificationRequest.model_validate(
        switch["__interrupt__"][0].value
    )
    assert set(request.candidates) >= {Intent.TRACKING, Intent.POSTAGE}
    assert gateway.commands == []

    switched = asyncio.run(
        runtime.resume(
            thread_id=thread_id,
            selected_intent=Intent.POSTAGE,
        )
    )

    assert switched["phase"] == "handoff"
    assert switched["active_intent"] == "postage"
    assert gateway.commands == []


def test_cancel_during_slot_collection_is_a_safe_terminal_control() -> None:
    runtime, gateway = _runtime()
    thread_id = "phase2-cancel"
    asyncio.run(
        runtime.start(thread_id=thread_id, message="查一下邮件")
    )

    cancelled = asyncio.run(
        runtime.resume(thread_id=thread_id, message="取消")
    )

    assert cancelled["phase"] == "completed"
    assert cancelled["active_intent"] is None
    assert cancelled["result"] is None
    assert cancelled["reply"] == "已取消当前查询。"
    assert gateway.commands == []


def test_non_integrated_intent_can_collect_slots_before_safe_handoff() -> None:
    runtime, gateway = _runtime()
    thread_id = "phase2-postage-slots"

    waiting = asyncio.run(
        runtime.start(thread_id=thread_id, message="帮我算邮费")
    )
    assert waiting["phase"] == "waiting_user"
    assert {
        item["name"] for item in waiting["required_inputs"]
    } == {"origin", "destination", "weight"}

    result = asyncio.run(
        runtime.resume(
            thread_id=thread_id,
            message="从北京寄到上海 2.5 公斤",
        )
    )

    assert result["phase"] == "handoff"
    assert result["active_intent"] == "postage"
    snapshot = asyncio.run(
        runtime.graph.aget_state(runtime.config(thread_id))
    )
    assert snapshot.values["slots"]["origin"]["canonical_name"] == "北京市"
    assert snapshot.values["slots"]["destination"]["canonical_name"] == "上海市"
    assert snapshot.values["slots"]["weight"] == {
        "value": "2.5",
        "unit": "kg",
    }
    assert gateway.commands == []


def test_slot_only_replies_are_merged_against_expected_fields() -> None:
    delivery_runtime, _ = _runtime()
    delivery_thread = "phase2-single-region"
    waiting_region = asyncio.run(
        delivery_runtime.start(
            thread_id=delivery_thread,
            message="从北京寄出大概需要几天",
        )
    )
    assert [
        item["name"] for item in waiting_region["required_inputs"]
    ] == ["destination"]
    delivery = asyncio.run(
        delivery_runtime.resume(
            thread_id=delivery_thread,
            message="上海",
        )
    )
    assert delivery["phase"] == "handoff"
    delivery_state = asyncio.run(
        delivery_runtime.graph.aget_state(
            delivery_runtime.config(delivery_thread)
        )
    )
    assert delivery_state.values["slots"]["origin"][
        "canonical_name"
    ] == "北京市"
    assert delivery_state.values["slots"]["destination"][
        "canonical_name"
    ] == "上海市"

    postage_runtime, _ = _runtime()
    postage_thread = "phase2-weight-only"
    waiting_weight = asyncio.run(
        postage_runtime.start(
            thread_id=postage_thread,
            message="从北京寄到上海，帮我算邮费",
        )
    )
    assert [
        item["name"] for item in waiting_weight["required_inputs"]
    ] == ["weight"]
    postage = asyncio.run(
        postage_runtime.resume(
            thread_id=postage_thread,
            message="2 公斤",
        )
    )
    assert postage["phase"] == "handoff"
    postage_state = asyncio.run(
        postage_runtime.graph.aget_state(
            postage_runtime.config(postage_thread)
        )
    )
    assert postage_state.values["slots"]["weight"] == {
        "value": "2",
        "unit": "kg",
    }
    assert postage_state.values["missing_slots"] == []

    partial_runtime, _ = _runtime()
    partial_thread = "phase2-destination-before-weight"
    partial = asyncio.run(
        partial_runtime.start(
            thread_id=partial_thread,
            message="从北京寄出要多少邮费",
        )
    )
    assert [item["name"] for item in partial["required_inputs"]] == [
        "destination",
        "weight",
    ]
    still_waiting = asyncio.run(
        partial_runtime.resume(
            thread_id=partial_thread,
            message="上海",
        )
    )
    assert [
        item["name"] for item in still_waiting["required_inputs"]
    ] == ["weight"]
    partial_state = asyncio.run(
        partial_runtime.graph.aget_state(
            partial_runtime.config(partial_thread)
        )
    )
    assert partial_state.values["slots"]["origin"][
        "canonical_name"
    ] == "北京市"
    assert partial_state.values["slots"]["destination"][
        "canonical_name"
    ] == "上海市"

    ambiguous_runtime, _ = _runtime()
    ambiguous_thread = "phase2-resolve-ambiguous-region"
    ambiguous = asyncio.run(
        ambiguous_runtime.start(
            thread_id=ambiguous_thread,
            message="从朝阳区寄到上海要多久",
        )
    )
    assert [item["name"] for item in ambiguous["required_inputs"]] == [
        "origin"
    ]
    resolved = asyncio.run(
        ambiguous_runtime.resume(
            thread_id=ambiguous_thread,
            message="北京朝阳",
        )
    )
    assert resolved["phase"] == "handoff"
    resolved_state = asyncio.run(
        ambiguous_runtime.graph.aget_state(
            ambiguous_runtime.config(ambiguous_thread)
        )
    )
    assert resolved_state.values["slots"]["origin"][
        "canonical_name"
    ] == "北京市朝阳区"


def test_conflicting_slot_is_preserved_until_confirmed_correction() -> None:
    runtime, _ = _runtime()
    thread_id = "phase2-correction"
    first = asyncio.run(
        runtime.start(
            thread_id=thread_id,
            message="从北京寄到上海，帮我算邮费",
        )
    )
    assert [item["name"] for item in first["required_inputs"]] == [
        "weight"
    ]

    conflict = asyncio.run(
        runtime.resume(
            thread_id=thread_id,
            message="改成从广州寄到上海 2 公斤",
        )
    )
    assert conflict["phase"] == "waiting_user"
    assert [item["name"] for item in conflict["required_inputs"]] == [
        "origin"
    ]
    assert "确认是否覆盖" in conflict["reply"]
    assert "confirm_overwrite=true" in conflict["required_inputs"][0][
        "validation_hint"
    ]
    snapshot = asyncio.run(
        runtime.graph.aget_state(runtime.config(thread_id))
    )
    assert snapshot.values["slots"]["origin"]["canonical_name"] == "北京市"

    corrected = asyncio.run(
        runtime.resume(
            thread_id=thread_id,
            message="从广州寄到上海 2 公斤",
            confirm_overwrite=True,
        )
    )
    assert corrected["phase"] == "handoff"
    snapshot = asyncio.run(
        runtime.graph.aget_state(runtime.config(thread_id))
    )
    assert snapshot.values["slots"]["origin"]["canonical_name"] == "广州市"
    assert any(
        event["event_type"] == "slot_conflict_detected"
        for event in snapshot.values["audit_events"]
    )
