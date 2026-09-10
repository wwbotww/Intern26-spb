from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from spb_assistant_api.adapters.checkpointer_factory import create_in_memory_checkpointer
from spb_assistant_api.adapters.fake_shipping import FakePostageGateway
from spb_assistant_api.adapters.in_memory_receipts import InMemoryToolExecutionRepository
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.failures import AgentFailure, FailureCategory
from spb_assistant_api.services.query_understanding import RuleBasedQueryUnderstander
from spb_assistant_api.workflow.composition import create_agent_runtime, create_persistent_agent

from .postage_p1_fixture import NOW, ObservedFakePostageGateway, preflight


MESSAGE = "北京寄上海 1.25 kg 查资费 SYN-A"
THREAD = "synthetic-postage-p1"


@asynccontextmanager
async def runtime(tmp_path, backend, gateway, *, policy=None, understander=None):
    kwargs = dict(postage_gateway=gateway, postage_preflight=policy or preflight(), clock=lambda: NOW, understander=understander, workflow_trace_sink=None)
    if backend == "sqlite":
        async with create_persistent_agent(database_path=tmp_path / "postage.db", **kwargs) as parts:
            yield parts.runtime
    else:
        yield create_agent_runtime(checkpointer=create_in_memory_checkpointer(), receipts=InMemoryToolExecutionRepository(), **kwargs)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_collect_product_then_execute_explicit_quote(tmp_path, backend):
    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, backend, gateway) as agent:
            waiting = await agent.start(thread_id=THREAD, message="北京寄上海 1.25 kg 查资费")
            assert waiting["phase"] == "waiting_user"
            assert [i["name"] for i in waiting["required_inputs"]] == ["product_code"]
            assert waiting["required_inputs"][0]["choices"] == ["SYN-A", "SYN-B"]
            assert gateway.commands == []
            done = await agent.resume(thread_id=THREAD, message="SYN-A")
            assert done["phase"] == "completed"
            assert done["result"]["data"]["quote_basis"]["context"]["weight_grams"] == 1250
            assert done["result"]["data"]["amount"] == "12.30"
            assert len(gateway.commands) == 1
    asyncio.run(run())


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_product_change_requires_confirmation_without_losing_weight(tmp_path, backend):
    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, backend, gateway) as agent:
            await agent.start(thread_id=THREAD, message="北京寄上海查资费 SYN-A")
            conflict = await agent.resume(thread_id=THREAD, message="SYN-B 1.25 kg")
            assert conflict["phase"] == "waiting_user"
            assert "product_code" in [v["name"] for v in conflict["required_inputs"]]
            assert gateway.commands == []
            done = await agent.resume(thread_id=THREAD, message="SYN-B", confirm_overwrite=True)
            assert done["phase"] == "completed"
            assert gateway.commands[0].product_code == "SYN-B"
            assert gateway.commands[0].pricing_context.weight_grams == 1250
    asyncio.run(run())


@pytest.mark.parametrize("condition", ["大箱子", "长宽高 10x20x30", "保价500", "回执", "客户价", "到站优惠", "国际件", "寄香港", "最便宜", "不需要保价"])
def test_unsupported_conditions_fail_closed_before_collecting_or_calling(tmp_path, condition):
    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, "memory", gateway) as agent:
            result = await agent.start(thread_id=THREAD, message=f"查资费 {condition}", explicit_intent=Intent.POSTAGE)
            assert result["phase"] == "failed"
            assert result["failure"]["code"] == "postage_scope_not_supported"
            assert result["required_inputs"] == []
            assert result["result"] is None
            assert "未生成报价" in result["reply"]
            assert gateway.commands == []
    asyncio.run(run())


@pytest.mark.parametrize("weight", [
    "0 kg", "-1 kg", "0.1 g", "100 kg", "NaN g", "Infinity kg",
    "1e3 kg", "1,500 g", "二公斤", "0.5 t", "- 1 kg", "−1 kg",
])
def test_invalid_replacement_weight_cannot_reuse_previous_valid_weight(tmp_path, weight):
    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, "memory", gateway) as agent:
            await agent.start(thread_id=THREAD, message="北京寄上海 1.25 kg 查资费")
            result = await agent.resume(thread_id=THREAD, message=f"SYN-A 改成 {weight}")
            assert result["phase"] == "failed"
            assert result["failure"]["code"] == "postage_weight_not_supported"
            assert gateway.commands == []
    asyncio.run(run())


def test_resolved_but_unmapped_destination_still_requires_input(tmp_path):
    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, "memory", gateway) as agent:
            waiting = await agent.start(thread_id=THREAD, message="北京寄广州 1.25 kg 查资费 SYN-A")
            assert waiting["phase"] == "waiting_user"
            assert [i["name"] for i in waiting["required_inputs"]] == ["destination"]
            assert gateway.commands == []
    asyncio.run(run())


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_new_query_is_fresh_and_checkpoint_receipt_replay_is_not(tmp_path, backend):
    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, backend, gateway) as agent:
            first = await agent.start(thread_id=THREAD, message=MESSAGE)
            history = [s async for s in agent.graph.aget_state_history(agent.config(THREAD))]
            before = next(s for s in history if s.next == ("execute_tool",))
            second = await agent.start(thread_id=THREAD, message=MESSAGE)
            assert first["phase"] == second["phase"] == "completed"
            assert first["result"]["data"]["queried_at"] != second["result"]["data"]["queried_at"]
            replay = await agent.graph.ainvoke(None, config=before.config)
            assert replay["result"] == first["result"]
            assert len(gateway.commands) == 2
    asyncio.run(run())


def test_sqlite_restart_keeps_the_pricing_context(tmp_path):
    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, "sqlite", gateway) as agent:
            await agent.start(thread_id=THREAD, message="北京寄上海 1.25 kg 查资费")
        async with runtime(tmp_path, "sqlite", gateway) as agent:
            done = await agent.resume(thread_id=THREAD, message="SYN-A")
            assert done["phase"] == "completed"
            assert len(gateway.commands) == 1
    asyncio.run(run())


def test_same_version_configuration_change_requires_restart(tmp_path):
    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, "sqlite", gateway) as agent:
            await agent.start(thread_id=THREAD, message="北京寄上海 1.25 kg 查资费")
        async with runtime(tmp_path, "sqlite", gateway, policy=preflight(currency="USD")) as agent:
            result = await agent.resume(thread_id=THREAD, message="SYN-A")
            assert result["failure"]["code"] == "postage_context_changed_restart"
            assert gateway.commands == []
            restarted = await agent.start(thread_id=THREAD, message=MESSAGE)
            assert restarted["phase"] == "completed"
            assert gateway.commands[0].pricing_context.currency == "USD"
    asyncio.run(run())


def test_old_sqlite_pause_is_not_certified_from_only_resume_text(tmp_path):
    async def run():
        async with create_persistent_agent(database_path=tmp_path / "postage.db", postage_gateway=FakePostageGateway(), clock=lambda: NOW, workflow_trace_sink=None) as parts:
            await parts.runtime.start(thread_id=THREAD, message="北京寄上海查资费")
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, "sqlite", gateway) as agent:
            result = await agent.resume(thread_id=THREAD, message="SYN-A 1.25 kg")
            assert result["failure"]["code"] == "postage_context_changed_restart"
            assert gateway.commands == []
    asyncio.run(run())


def test_model_product_is_not_trusted_and_unavailable_capability_does_not_collect(tmp_path):
    class ForgedProduct:
        async def understand(self, **kwargs):
            result = await RuleBasedQueryUnderstander().understand(**kwargs)
            return result.model_copy(update={"slots": result.slots.model_copy(update={"product_code": "MODEL-INVENTED"})})

    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, "memory", gateway, understander=ForgedProduct()) as agent:
            result = await agent.start(thread_id=THREAD, message="北京寄上海 1.25 kg 查资费")
            assert result["phase"] == "waiting_user"
            assert gateway.commands == []
        agent = create_agent_runtime(checkpointer=create_in_memory_checkpointer(), receipts=InMemoryToolExecutionRepository(), postage_preflight=preflight(), clock=lambda: NOW, workflow_trace_sink=None)
        result = await agent.start(thread_id=THREAD, message="查资费")
        assert result["phase"] == "handoff"
        assert result["required_inputs"] == []
    asyncio.run(run())


def test_known_and_unknown_product_is_not_silently_reduced_to_one_quote(tmp_path):
    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, "memory", gateway) as agent:
            result = await agent.start(thread_id=THREAD, message="北京寄上海 1.25 kg 查资费，产品 UNKNOWN 或 SYN-A")
            assert result["phase"] == "waiting_user"
            assert gateway.commands == []
    asyncio.run(run())


def test_cancel_and_new_query_clear_quotation_context(tmp_path):
    async def run():
        gateway = ObservedFakePostageGateway()
        async with runtime(tmp_path, "memory", gateway) as agent:
            await agent.start(thread_id=THREAD, message="北京寄上海查资费 SYN-A")
            result = await agent.resume(thread_id=THREAD, message="取消")
            assert result["phase"] == "completed"
            snapshot = await agent.graph.aget_state(agent.config(THREAD))
            assert snapshot.values["postage_policy_snapshot"] is None
            assert snapshot.values["postage_requirements"] == []
            done = await agent.start(thread_id=THREAD, message=MESSAGE)
            assert done["phase"] == "completed"
            assert len(gateway.commands) == 1
    asyncio.run(run())


def test_invalid_quote_never_creates_a_receipt(tmp_path):
    class WrongProduct(ObservedFakePostageGateway):
        async def quote(self, cmd):
            observed = await super().quote(cmd)
            return observed.model_copy(update={"data": observed.data.model_copy(update={"product_code": "SYN-B"})})

    async def run():
        gateway = WrongProduct()
        receipts = InMemoryToolExecutionRepository()
        agent = create_agent_runtime(
            checkpointer=create_in_memory_checkpointer(), receipts=receipts,
            postage_gateway=gateway, postage_preflight=preflight(),
            clock=lambda: NOW, workflow_trace_sink=None,
        )
        result = await agent.start(thread_id=THREAD, message=MESSAGE)
        assert result["phase"] == "failed"
        assert result["result"] is None
        assert len(receipts) == 0
        assert len(gateway.commands) == 1
    asyncio.run(run())


def test_only_graph_retries_and_preserves_pricing_context(tmp_path):
    class Transient(ObservedFakePostageGateway):
        def __init__(self):
            super().__init__()
            self.failed_command = None

        async def quote(self, cmd):
            if self.failed_command is None:
                self.failed_command = cmd
                raise AgentOperationError(AgentFailure(
                    category=FailureCategory.UPSTREAM_TIMEOUT,
                    code="synthetic_timeout", message="合成超时", retryable=True,
                ))
            return await super().quote(cmd)

    async def run():
        gateway = Transient()
        async with runtime(tmp_path, "memory", gateway) as agent:
            result = await agent.start(thread_id=THREAD, message=MESSAGE)
            assert result["phase"] == "completed"
            snapshot = await agent.graph.aget_state(agent.config(THREAD))
            assert snapshot.values["retry_count"] == 1
            assert snapshot.values["tool_call_count"] == 1
            assert gateway.failed_command == gateway.commands[0]
    asyncio.run(run())
