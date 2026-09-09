from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from spb_assistant_api.adapters.checkpointer_factory import create_in_memory_checkpointer
from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.adapters.in_memory_receipts import InMemoryToolExecutionRepository
from spb_assistant_api.adapters.sqlite_persistence import create_sqlite_agent_repositories
from spb_assistant_api.domain.agent_actions import InvokeToolAction
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.agent_events import ToolCallRecord, ToolCallStatus
from spb_assistant_api.domain.commands import TrackingCommand
from spb_assistant_api.domain.failures import AgentFailure, FailureCategory
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.domain.results import AgentResult, AgentResultStatus, TrackingData, TrackingEvent
from spb_assistant_api.domain.tooling import ToolExecutionReceipt, argument_fingerprint
from spb_assistant_api.services.agent_tools import AgentCommandDispatcher, AgentToolRegistry, ToolExecutor
from spb_assistant_api.tools.tracking import TrackingTool
from spb_assistant_api.workflow.composition import create_agent_runtime, create_persistent_agent
from spb_assistant_api.workflow.policy import WorkflowPolicy


NOW = datetime(2026, 9, 9, 2, tzinfo=UTC)
MAIL = "1234567890123"
THREAD = "synthetic-query-scope"
QUERY = UUID("22222222-2222-4222-8222-222222222222")


def _data(status="运输中"):
    return TrackingData(
        mail_no=MAIL, current_status=status, queried_at=NOW,
        events=[TrackingEvent(description=f"合成{status}节点", occurred_at=NOW)],
    )


@asynccontextmanager
async def _runtime(tmp_path, backend, gateway, clock=lambda: NOW):
    if backend == "sqlite":
        async with create_persistent_agent(
            database_path=tmp_path / "scope.db", tracking_gateway=gateway,
            clock=clock,
        ) as components:
            yield components.runtime
    else:
        yield create_agent_runtime(
            checkpointer=create_in_memory_checkpointer(),
            receipts=InMemoryToolExecutionRepository(),
            tracking_gateway=gateway, clock=clock,
        )


def _action(registry, *, query_id=QUERY, **changes):
    state = {
        "conversation_id": THREAD, "query_id": str(query_id),
        "active_intent": "tracking", "slots": {"intent": "tracking", "mail_no": MAIL},
        "deadline_at": (NOW + timedelta(seconds=30)).isoformat(), **changes,
    }
    return WorkflowPolicy(registry.descriptors).decide(state)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("initial_empty", [False, True])
def test_new_query_is_fresh_but_historical_checkpoint_replay_is_not(tmp_path, backend, initial_empty):
    async def scenario():
        current = [NOW]
        gateway = FakeTrackingGateway(
            {} if initial_empty else {MAIL: _data()}, clock=lambda: current[0]
        )
        async with _runtime(tmp_path, backend, gateway, lambda: current[0]) as runtime:
            first = await runtime.start(thread_id=THREAD, message=f"查邮件 {MAIL}")
            history = [s async for s in runtime.graph.aget_state_history(runtime.config(THREAD))]
            before_first = next(s for s in history if s.next == ("execute_tool",))
            first_id = before_first.values["pending_action"]["tool_call_id"]
            first_query = before_first.values["query_id"]
            assert first["result"]["status"] == ("no_match" if initial_empty else "success")

            current[0] += timedelta(minutes=1)
            gateway._records[MAIL] = _data("已签收")
            second = await runtime.start(thread_id=THREAD, message=f"再查邮件 {MAIL}")
            assert second["result"]["data"]["current_status"] == "已签收"
            assert datetime.fromisoformat(second["result"]["data"]["queried_at"]) == current[0]
            latest = await runtime.graph.aget_state(runtime.config(THREAD))
            assert latest.values["query_id"] != first_query
            assert latest.values["tool_call_count"] == 1
            assert latest.values["pending_action"]["tool_call_id"] != first_id
            assert len(gateway.commands) == 2

            replay = await runtime.graph.ainvoke(None, config=before_first.config)
            assert replay["result"] == first["result"]
            assert len(gateway.commands) == 2
            snapshot = await runtime.graph.aget_state(runtime.config(THREAD))
            assert snapshot.values["query_id"] == first_query
            assert snapshot.values["tool_calls"][-1]["status"] == "reused"
            assert "query_id" not in replay
            assert "legacy_tool_call" not in replay

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_query_identity_survives_clarification_and_retry(tmp_path, backend):
    async def scenario():
        gateway = FakeTrackingGateway(
            {MAIL: _data()}, clock=lambda: NOW,
            scripted_failures=[AgentFailure(
                category=FailureCategory.UPSTREAM_TIMEOUT, code="synthetic_timeout",
                message="合成超时", retryable=True,
            )],
        )
        async with _runtime(tmp_path, backend, gateway) as runtime:
            waiting = await runtime.start(thread_id=THREAD, message="查邮件")
            paused = await runtime.graph.aget_state(runtime.config(THREAD))
            query_id = paused.values["query_id"]
            completed = await runtime.resume(thread_id=THREAD, message=MAIL)
            final = await runtime.graph.aget_state(runtime.config(THREAD))
            assert completed["phase"] == "completed"
            assert final.values["query_id"] == query_id
            assert completed["turn_id"] != waiting["turn_id"]
            assert final.values["tool_call_count"] == 1
            assert final.values["retry_count"] == 1
            assert len({row["tool_call_id"] for row in final.values["tool_calls"]}) == 1
            assert len(gateway.commands) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("directive", ["取消", "重新开始"])
def test_control_then_new_query_cannot_inherit_execution_identity(tmp_path, directive):
    async def scenario():
        gateway = FakeTrackingGateway({MAIL: _data()}, clock=lambda: NOW)
        async with _runtime(tmp_path, "memory", gateway) as runtime:
            await runtime.start(thread_id=THREAD, message="查邮件")
            old = await runtime.graph.aget_state(runtime.config(THREAD))
            cancelled = await runtime.resume(thread_id=THREAD, message=directive)
            assert cancelled["phase"] == "completed"
            assert gateway.commands == []
            completed = await runtime.start(thread_id=THREAD, message=f"查邮件 {MAIL}")
            new = await runtime.graph.aget_state(runtime.config(THREAD))
            assert completed["phase"] == "completed"
            assert new.values["query_id"] != old.values["query_id"]
            assert new.values["legacy_tool_call"] is None
            assert len(gateway.commands) == 1

    asyncio.run(scenario())


def test_budget_is_by_execution_identity_not_prior_argument_match():
    registry = AgentToolRegistry([TrackingTool(FakeTrackingGateway())])
    first = _action(registry).action
    assert isinstance(first, InvokeToolAction)
    history = [ToolCallRecord(
        tool_call_id=first.tool_call_id, tool_name="tracking",
        argument_fingerprint=first.argument_fingerprint,
        attempt=1, status=ToolCallStatus.SUCCEEDED,
    ).model_dump(mode="json")]
    new = _action(registry, query_id=uuid4(), tool_calls=history).action
    assert isinstance(new, InvokeToolAction)
    assert new.argument_fingerprint == first.argument_fingerprint
    assert new.tool_call_id != first.tool_call_id
    blocked = _action(registry, query_id=uuid4(), tool_calls=history, tool_call_count=1)
    assert blocked.failure.code == "tool_call_budget_exceeded"
    retry = _action(registry, tool_calls=history, tool_call_count=1, retry_count=1).action
    assert isinstance(retry, InvokeToolAction)
    assert retry.tool_call_id == first.tool_call_id and retry.attempt == 2


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_receipts_distinguish_execution_ids_and_reject_same_id_conflicts(tmp_path, backend):
    @asynccontextmanager
    async def repository():
        if backend == "sqlite":
            async with create_sqlite_agent_repositories(tmp_path / "receipts.db") as repos:
                yield repos.tool_receipts
        else:
            yield InMemoryToolExecutionRepository()

    async def scenario():
        result = await TrackingTool(FakeTrackingGateway(clock=lambda: NOW)).execute(TrackingCommand(mail_no=MAIL))
        first = ToolExecutionReceipt(
            conversation_id=THREAD, tool_call_id=uuid4(), tool_name="tracking",
            argument_fingerprint=argument_fingerprint(TrackingCommand(mail_no=MAIL)),
            result=result, completed_at=NOW,
        )
        second = first.model_copy(update={"tool_call_id": uuid4()})
        async with repository() as receipts:
            await receipts.save(first)
            await receipts.save(first)
            await receipts.save(second)
            assert await receipts.find(conversation_id=THREAD, tool_call_id=first.tool_call_id) == first
            assert await receipts.find(conversation_id="other", tool_call_id=first.tool_call_id) is None
            with pytest.raises(AgentOperationError) as error:
                await receipts.save(first.model_copy(update={"argument_fingerprint": "sha256:changed"}))
            assert error.value.failure.code == "tool_receipt_conflict"
            assert await receipts.delete_conversation(THREAD) == 2
            assert await receipts.delete_conversation(THREAD) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["conversation_id", "tool_name", "tool_call_id", "argument_fingerprint", "result_tool"])
def test_executor_rejects_corrupt_receipt_identity_without_calling_gateway(field):
    async def scenario():
        gateway = FakeTrackingGateway(clock=lambda: NOW)
        registry = AgentToolRegistry([TrackingTool(gateway)])
        action = _action(registry).action
        receipt = ToolExecutionReceipt(
            conversation_id=THREAD, tool_call_id=action.tool_call_id, tool_name="tracking",
            argument_fingerprint=action.argument_fingerprint, completed_at=NOW,
            result=AgentResult(tool="tracking", intent=Intent.TRACKING, status=AgentResultStatus.NO_MATCH),
        )
        if field == "result_tool":
            receipt = receipt.model_copy(update={"result": receipt.result.model_copy(update={"tool": "wrong"})})
        else:
            receipt = receipt.model_copy(update={field: uuid4() if field == "tool_call_id" else "wrong"})
        receipts = InMemoryToolExecutionRepository()
        receipts._receipts[(THREAD, action.tool_call_id)] = receipt
        executor = ToolExecutor(AgentCommandDispatcher(registry), receipts, clock=lambda: NOW)
        with pytest.raises(AgentOperationError) as error:
            await executor.execute(conversation_id=THREAD, action=action)
        assert error.value.failure.code == "receipt_identity_mismatch"
        assert gateway.commands == []

    asyncio.run(scenario())


def test_argument_integrity_is_checked_even_when_execution_id_matches():
    async def scenario():
        gateway = FakeTrackingGateway(clock=lambda: NOW)
        registry = AgentToolRegistry([TrackingTool(gateway)])
        action = _action(registry).action.model_copy(update={"command": TrackingCommand(mail_no="0000000000000")})
        executor = ToolExecutor(AgentCommandDispatcher(registry), InMemoryToolExecutionRepository(), clock=lambda: NOW)
        with pytest.raises(AgentOperationError) as error:
            await executor.execute(conversation_id=THREAD, action=action)
        assert error.value.failure.code == "action_argument_fingerprint_mismatch"
        assert gateway.commands == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("case", "code"),
    [("empty", "tracking_events_missing"), ("status", "tracking_status_missing"),
     ("description", "tracking_event_description_missing"), ("time", "tracking_event_time_without_timezone")],
)
def test_invalid_tracking_facts_are_neither_cached_nor_displayed(case, code):
    async def scenario():
        data = _data()
        if case == "empty":
            data.events = []
        elif case == "status":
            data.current_status = " "
        elif case == "description":
            data.events[0].description = " "
        else:
            data.events[0].occurred_at = NOW.replace(tzinfo=None)
        gateway = FakeTrackingGateway({MAIL: data}, clock=lambda: NOW)
        receipts = InMemoryToolExecutionRepository()
        runtime = create_agent_runtime(
            checkpointer=create_in_memory_checkpointer(), receipts=receipts,
            tracking_gateway=gateway, clock=lambda: NOW,
        )
        result = await runtime.start(thread_id=THREAD, message=f"查邮件 {MAIL}")
        assert result["phase"] == "failed" and result["result"] is None
        assert result["failure"]["code"] == code
        assert len(receipts) == 0 and len(gateway.commands) == 1
        snapshot = await runtime.graph.aget_state(runtime.config(THREAD))
        assert snapshot.values["last_result"] is None

    asyncio.run(scenario())
