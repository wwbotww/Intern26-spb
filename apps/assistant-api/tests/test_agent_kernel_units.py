from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import BaseModel

from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.adapters.in_memory_receipts import (
    InMemoryToolExecutionRepository,
)
from spb_assistant_api.domain.agent_actions import (
    ClarifyIntentAction,
    CollectSlotsAction,
    HandoffAction,
    InvokeToolAction,
    RequiredInput,
    RespondAction,
    UnderstandAction,
    ValidateResultAction,
)
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.commands import TrackingCommand
from spb_assistant_api.domain.failures import AgentFailure, FailureCategory
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.domain.results import (
    AgentResult,
    AgentResultStatus,
    TrackingData,
    TrackingEvent,
)
from spb_assistant_api.domain.tooling import ToolDescriptor
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
from spb_assistant_api.workflow.policy import WorkflowPolicy
from spb_assistant_api.workflow.reducers import (
    append_events,
    append_tool_calls,
)
from spb_assistant_api.workflow.routing import (
    route_after_validation,
    route_next_action,
)


NOW = datetime(2026, 9, 3, tzinfo=UTC)
MAIL_NO = "1234567890123"


def _registry(
    gateway: FakeTrackingGateway | None = None,
) -> AgentToolRegistry:
    return AgentToolRegistry(
        [TrackingTool(gateway or FakeTrackingGateway())],
        required_intents=frozenset({Intent.TRACKING}),
    )


def _ready_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "conversation_id": "conversation-1",
        "active_intent": "tracking",
        "slots": {"intent": "tracking", "mail_no": MAIL_NO},
        "missing_slots": [],
        "tool_calls": [],
        "tool_call_count": 0,
        "retry_count": 0,
        "step_count": 1,
        "max_steps": 8,
        "max_tool_calls": 1,
        "deadline_at": NOW.isoformat(),
    }
    state.update(updates)
    return state


def test_reducers_append_without_mutating_inputs() -> None:
    existing = [{"event_type": "first"}]
    update = [{"event_type": "second"}]

    events = append_events(existing, update)
    calls = append_tool_calls(existing, update)

    assert events == [*existing, *update]
    assert calls == [*existing, *update]
    assert events is not existing
    assert calls is not existing
    assert existing == [{"event_type": "first"}]


def test_rule_understander_extracts_supported_mail_number_formats() -> None:
    understander = RuleBasedQueryUnderstander()

    domestic = asyncio.run(
        understander.understand(message=f"查询邮件 {MAIL_NO}")
    )
    international = asyncio.run(
        understander.understand(message="查询 RR123456789CN")
    )

    assert domestic.selected_intent is Intent.TRACKING
    assert domestic.slots is not None
    assert domestic.slots.mail_no == MAIL_NO
    assert international.slots is not None
    assert international.slots.mail_no == "RR123456789CN"


def test_active_workflow_treats_resume_value_as_tracking_input() -> None:
    result = asyncio.run(
        RuleBasedQueryUnderstander().understand(
            message=MAIL_NO,
            active_intent=Intent.TRACKING,
        )
    )

    assert result.source == "active_workflow"
    assert result.missing_slots == []


def test_agent_registry_rejects_duplicate_or_missing_intents() -> None:
    tool = TrackingTool(FakeTrackingGateway())

    with pytest.raises(ValueError, match="只能注册一个"):
        AgentToolRegistry([tool, tool])
    with pytest.raises(ValueError, match="tracking"):
        AgentToolRegistry([], required_intents=frozenset({Intent.TRACKING}))


def test_tool_descriptor_rejects_command_for_another_intent() -> None:
    with pytest.raises(ValueError, match="intent"):
        ToolDescriptor(
            intent=Intent.POLICY,
            tool_name="wrong",
            command_type=TrackingCommand,
            result_schema_name="PolicyData",
            required_slots=("question",),
        )


def test_dispatcher_rejects_arbitrary_tool_name_before_execution() -> None:
    gateway = FakeTrackingGateway()
    dispatcher = AgentCommandDispatcher(_registry(gateway))

    with pytest.raises(AgentOperationError) as raised:
        asyncio.run(
            dispatcher.dispatch(
                tool_name="user_supplied_tool",
                command=TrackingCommand(mail_no=MAIL_NO),
            )
        )

    assert raised.value.failure.code == "tool_route_mismatch"
    assert gateway.commands == []


def test_executor_stops_at_deadline_before_gateway_call() -> None:
    gateway = FakeTrackingGateway()
    registry = _registry(gateway)
    policy = WorkflowPolicy(registry.descriptors)
    decision = policy.decide(
        _ready_state(deadline_at=(NOW - timedelta(seconds=1)).isoformat())
    )
    assert isinstance(decision.action, InvokeToolAction)
    executor = ToolExecutor(
        AgentCommandDispatcher(registry),
        InMemoryToolExecutionRepository(),
        clock=lambda: NOW,
    )

    with pytest.raises(AgentOperationError) as raised:
        asyncio.run(
            executor.execute(
                conversation_id="conversation-1",
                action=decision.action,
            )
        )

    assert raised.value.failure.code == "request_deadline_exceeded"
    assert gateway.commands == []


def test_executor_reuses_receipt_even_after_action_deadline() -> None:
    gateway = FakeTrackingGateway()
    registry = _registry(gateway)
    policy = WorkflowPolicy(registry.descriptors)
    decision = policy.decide(
        _ready_state(deadline_at=(NOW + timedelta(seconds=1)).isoformat())
    )
    assert isinstance(decision.action, InvokeToolAction)
    receipts = InMemoryToolExecutionRepository()
    executor = ToolExecutor(
        AgentCommandDispatcher(registry),
        receipts,
        clock=lambda: NOW,
    )
    first = asyncio.run(
        executor.execute(
            conversation_id="conversation-1",
            action=decision.action,
        )
    )
    expired = decision.action.model_copy(
        update={"deadline_at": NOW - timedelta(seconds=1)}
    )
    second = asyncio.run(
        executor.execute(
            conversation_id="conversation-1",
            action=expired,
        )
    )

    assert not first.reused
    assert second.reused
    assert len(gateway.commands) == 1


def test_policy_builds_typed_deterministic_tool_action() -> None:
    policy = WorkflowPolicy(_registry().descriptors)

    first = policy.decide(_ready_state())
    second = policy.decide(_ready_state())

    assert isinstance(first.action, InvokeToolAction)
    assert isinstance(first.action.command, TrackingCommand)
    assert first.action.command.mail_no == MAIL_NO
    assert first.action.argument_fingerprint.startswith("sha256:")
    assert first.action.tool_call_id == second.action.tool_call_id


def test_policy_collects_missing_slot_and_enforces_step_budget() -> None:
    policy = WorkflowPolicy(_registry().descriptors)

    missing = policy.decide(
        _ready_state(
            slots={"intent": "tracking", "mail_no": None},
            missing_slots=["mail_no"],
        )
    )
    exhausted = policy.decide(_ready_state(step_count=9, max_steps=8))

    assert isinstance(missing.action, CollectSlotsAction)
    assert [item.name for item in missing.action.required_inputs] == [
        "mail_no"
    ]
    assert isinstance(exhausted.action, RespondAction)
    assert exhausted.failure is not None
    assert (
        exhausted.failure.category
        is FailureCategory.LOOP_BUDGET_EXCEEDED
    )


def test_policy_enforces_unique_tool_call_budget() -> None:
    decision = WorkflowPolicy(_registry().descriptors).decide(
        _ready_state(tool_call_count=1)
    )

    assert isinstance(decision.action, RespondAction)
    assert decision.failure is not None
    assert decision.failure.code == "tool_call_budget_exceeded"


def test_policy_rejects_slots_that_cannot_build_valid_command() -> None:
    decision = WorkflowPolicy(_registry().descriptors).decide(
        _ready_state(slots={"intent": "tracking", "mail_no": "bad value"})
    )

    assert isinstance(decision.action, RespondAction)
    assert decision.failure is not None
    assert decision.failure.category is FailureCategory.INVALID_INPUT


def test_recovery_policy_only_retries_allowlisted_transient_failure() -> None:
    timeout = AgentFailure(
        category=FailureCategory.UPSTREAM_TIMEOUT,
        code="timeout",
        message="timeout",
        retryable=True,
    )
    contract = AgentFailure(
        category=FailureCategory.CONTRACT_VIOLATION,
        code="bad_contract",
        message="bad contract",
        retryable=True,
    )

    assert WorkflowPolicy.recover(
        timeout,
        retry_count=0,
        max_retries=1,
    ).retry
    assert not WorkflowPolicy.recover(
        timeout,
        retry_count=1,
        max_retries=1,
    ).retry
    assert not WorkflowPolicy.recover(
        contract,
        retry_count=0,
        max_retries=1,
    ).retry


def test_tracking_result_validator_rejects_wrong_mail_or_event_order() -> None:
    command = TrackingCommand(mail_no=MAIL_NO)
    wrong_mail = AgentResult(
        tool="tracking",
        intent=Intent.TRACKING,
        status=AgentResultStatus.SUCCESS,
        answer="ok",
        data=TrackingData(
            mail_no="9999999999999",
            current_status="运输中",
            queried_at=NOW,
        ),
    )
    wrong_order = AgentResult(
        tool="tracking",
        intent=Intent.TRACKING,
        status=AgentResultStatus.SUCCESS,
        answer="ok",
        data=TrackingData(
            mail_no=MAIL_NO,
            current_status="运输中",
            events=[
                TrackingEvent(description="第二步", occurred_at=NOW),
                TrackingEvent(
                    description="第一步",
                    occurred_at=datetime(2026, 9, 2, tzinfo=UTC),
                ),
            ],
            queried_at=NOW,
        ),
    )
    validator = AgentResultValidator()

    with pytest.raises(AgentOperationError) as mail_error:
        validator.validate(command=command, result=wrong_mail)
    with pytest.raises(AgentOperationError) as order_error:
        validator.validate(command=command, result=wrong_order)

    assert mail_error.value.failure.code == "tracking_mail_number_mismatch"
    assert order_error.value.failure.code == (
        "tracking_events_not_chronological"
    )


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (UnderstandAction(), "understand"),
        (
            ClarifyIntentAction(
                candidates=[Intent.TRACKING],
                prompt="请选择意图",
            ),
            "clarify",
        ),
        (
            CollectSlotsAction(
                intent=Intent.TRACKING,
                required_inputs=[
                    RequiredInput(name="mail_no", label="邮件号")
                ],
                prompt="请提供邮件号",
            ),
            "clarify",
        ),
        (
            InvokeToolAction(
                tool_name="tracking",
                command=TrackingCommand(mail_no=MAIL_NO),
                tool_call_id=uuid4(),
                argument_fingerprint="sha256:test",
                attempt=1,
                deadline_at=NOW,
            ),
            "execute_tool",
        ),
        (ValidateResultAction(), "validate_result"),
        (RespondAction(), "compose_response"),
        (
            HandoffAction(reason_code="unsupported"),
            "compose_response",
        ),
    ],
)
def test_typed_action_routing_is_total(
    action: BaseModel,
    expected: str,
) -> None:
    assert route_next_action(
        {"pending_action": action.model_dump(mode="json")}
    ) == expected


def test_validation_routing_rejects_unknown_phase() -> None:
    assert route_after_validation({"phase": "recovering"}) == "recover"
    assert route_after_validation({"phase": "responding"}) == (
        "compose_response"
    )
    with pytest.raises(ValueError, match="不可路由"):
        route_after_validation({"phase": "ready"})
