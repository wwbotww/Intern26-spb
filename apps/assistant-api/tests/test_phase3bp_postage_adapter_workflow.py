from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from itertools import count

import httpx
import pytest

from spb_assistant_api.adapters.checkpointer_factory import create_in_memory_checkpointer
from spb_assistant_api.adapters.in_memory_receipts import InMemoryToolExecutionRepository
from spb_assistant_api.workflow.composition import create_agent_runtime, create_persistent_agent

from .postage_p1_fixture import NOW
from .postage_p2_fixture import PRIVATE, bound_policy, config, fixture, gateway, request_form, response_for


MESSAGE = "北京寄上海 1.25 kg 查资费 SYN-A"
THREAD = "synthetic-postage-p2"


def test_sqlite_multi_turn_restart_receipt_replay_and_fresh_request(tmp_path):
    calls = []
    times = count()
    serials = count()
    def handler(request):
        calls.append(request)
        return response_for(request)
    async def run():
        client = gateway(handler, clock=lambda: NOW + timedelta(seconds=next(times)), serial_number_factory=lambda: f"synth-{next(serials)}")
        kwargs = dict(
            database_path=tmp_path / "postage.db", postage_gateway=client,
            postage_preflight=bound_policy(), clock=lambda: NOW, workflow_trace_sink=None,
        )
        try:
            async with create_persistent_agent(**kwargs) as parts:
                waiting = await parts.runtime.start(thread_id=THREAD, message="北京寄上海 1.25 kg 查资费")
                assert waiting["phase"] == "waiting_user"
                assert waiting["required_inputs"][0]["name"] == "product_code"
                assert calls == []
            async with create_persistent_agent(**kwargs) as parts:
                first = await parts.runtime.resume(thread_id=THREAD, message="SYN-A")
                assert first["phase"] == "completed"
                history = [s async for s in parts.runtime.graph.aget_state_history(parts.runtime.config(THREAD))]
                before = next(s for s in history if s.next == ("execute_tool",))
                first_time = first["result"]["data"]["queried_at"]
            # Complete receipt also survives process-resource reconstruction.
            async with create_persistent_agent(**kwargs) as parts:
                replay = await parts.runtime.graph.ainvoke(None, config=before.config)
                assert replay["result"] == first["result"]
                assert len(calls) == 1
                fresh = await parts.runtime.start(thread_id=THREAD, message=MESSAGE)
                assert fresh["phase"] == "completed"
                assert fresh["result"]["data"]["queried_at"] != first_time
                assert len(calls) == 2
                dumped = json.dumps(fresh, ensure_ascii=False)
                for private in (PRIVATE, "messageHeader", "_api_signature", "synthetic-password-only", "synthetic-sk-only"):
                    assert private not in dumped
                assert fresh["result"]["provenance"][0]["source_type"] == "fake_gateway"
        finally:
            await client.close()
    asyncio.run(run())
    assert [json.loads(request_form(r)["messageHeader"])["serialNo"] for r in calls] == ["synth-0", "synth-1"]


@pytest.mark.parametrize("new_version", ["1.0.0", "1.0.1"])
def test_sqlite_suspended_profile_drift_requires_new_query(tmp_path, new_version):
    calls = []
    async def run():
        first_client = gateway(response_for)
        try:
            async with create_persistent_agent(
                database_path=tmp_path / "state.db", postage_gateway=first_client,
                postage_preflight=bound_policy(), clock=lambda: NOW, workflow_trace_sink=None,
            ) as parts:
                await parts.runtime.start(thread_id=THREAD, message="北京寄上海 1.25 kg 查资费")
        finally:
            await first_client.close()
        cfg = config(profile={**fixture("profile.json"), "api_version": new_version})
        client = gateway(lambda r: calls.append(r) or response_for(r), cfg=cfg)
        try:
            async with create_persistent_agent(
                database_path=tmp_path / "state.db", postage_gateway=client,
                postage_preflight=bound_policy(cfg), clock=lambda: NOW, workflow_trace_sink=None,
            ) as parts:
                result = await parts.runtime.resume(thread_id=THREAD, message="SYN-A")
                if new_version == "1.0.0":
                    assert result["phase"] == "completed" and len(calls) == 1
                else:
                    assert result["failure"]["code"] == "postage_context_changed_restart"
                    assert calls == []
                    assert (await parts.runtime.start(thread_id=THREAD, message=MESSAGE))["phase"] == "completed"
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("code", ["0001", "0002", "0003", "0005", "0006", "0007", "0008", "0009", "0010", "0011", "0016"])
def test_business_refusal_is_final_without_success_receipt_or_price(code):
    calls = []
    receipts = InMemoryToolExecutionRepository()
    async def run():
        client = gateway(lambda r: calls.append(r) or response_for(r, errorcode=code, errorname=PRIVATE))
        try:
            runtime = create_agent_runtime(
                checkpointer=create_in_memory_checkpointer(), receipts=receipts,
                postage_gateway=client, postage_preflight=bound_policy(), clock=lambda: NOW, workflow_trace_sink=None,
            )
            result = await runtime.start(thread_id=THREAD, message=MESSAGE)
            assert result["phase"] == "failed" and result["result"] is None
            assert "未生成报价" in result["reply"]
            assert "稍后重试" not in result["reply"]
            assert PRIVATE not in json.dumps(result)
            assert len(calls) == 1 and len(receipts) == 0
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("recover", [False, True])
def test_graph_is_the_only_retry_owner_and_keeps_command_but_refreshes_serial(recover):
    calls = []
    ids = count()
    def handler(request):
        calls.append(request)
        if not recover or len(calls) == 1:
            raise httpx.ReadTimeout(PRIVATE, request=request)
        return response_for(request)
    async def run():
        client = gateway(handler, serial_number_factory=lambda: f"attempt-{next(ids)}")
        receipts = InMemoryToolExecutionRepository()
        try:
            runtime = create_agent_runtime(
                checkpointer=create_in_memory_checkpointer(), receipts=receipts,
                postage_gateway=client, postage_preflight=bound_policy(), clock=lambda: NOW, workflow_trace_sink=None,
            )
            result = await runtime.start(thread_id=THREAD, message=MESSAGE)
            assert result["phase"] == ("completed" if recover else "failed")
            assert len(calls) == 2 and len(receipts) == (1 if recover else 0)
            first, second = [request_form(r) for r in calls]
            assert first["map"] == second["map"]
            assert json.loads(first["messageHeader"])["serialNo"] != json.loads(second["messageHeader"])["serialNo"]
        finally:
            await client.close()
    asyncio.run(run())


def test_message_idempotency_replays_original_observation_without_gateway_call(tmp_path):
    calls = []
    async def run():
        client = gateway(lambda r: calls.append(r) or response_for(r))
        try:
            async with create_persistent_agent(
                database_path=tmp_path / "message.db", postage_gateway=client,
                postage_preflight=bound_policy(), clock=lambda: NOW, workflow_trace_sink=None,
            ) as parts:
                conversation = await parts.service.create_conversation(owner_id="synthetic-owner")
                kwargs = dict(conversation_id=conversation.conversation_id, owner_id="synthetic-owner", idempotency_key="same-message", message=MESSAGE)
                first = await parts.service.send_message(**kwargs)
                replay = await parts.service.send_message(**kwargs)
                assert first == replay and first["phase"] == "completed"
                assert len(calls) == 1
        finally:
            await client.close()
    asyncio.run(run())
