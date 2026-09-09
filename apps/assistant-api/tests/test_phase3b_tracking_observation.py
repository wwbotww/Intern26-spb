from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.adapters.postal_tracking import PostalTrackingGateway
from spb_assistant_api.adapters.postal_tracking_contract import PostalTrackingConfig
from spb_assistant_api.adapters.sqlite_persistence import SqliteConversationMetadataRepository
from spb_assistant_api.api.agent_contracts import AgentApiDependencies
from spb_assistant_api.api.app import create_app
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.commands import TrackingCommand
from spb_assistant_api.domain.results import TrackingData, TrackingEvent
from spb_assistant_api.domain.tracking import TrackingQueryResult, TrackingSource
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.tools.tracking import TrackingTool
from spb_assistant_api.workflow.composition import create_persistent_agent


NOW = datetime(2026, 9, 9, 2, tzinfo=UTC)
MAIL = "1234567890123"
THREAD = UUID("11111111-1111-4111-8111-111111111111")
FIXTURES = Path(__file__).parent / "fixtures" / "postal_tracking"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _data():
    return TrackingData(
        mail_no=MAIL, current_status="运输中", queried_at=NOW,
        events=[TrackingEvent(description="合成运输节点", occurred_at=NOW)],
    )


def _postal(handler, clock=lambda: NOW):
    return PostalTrackingGateway(
        PostalTrackingConfig(
            enabled=True, base_url="https://postal.example.test/api",
            send_id="SYNTHETIC", msg_kind="SYNTHETIC_JDPT_TRACE",
            signing_system_id="synthetic-system",
            expected_response_receive_id="SYNTHETIC", timezone="Asia/Shanghai",
        ),
        transport=httpx.MockTransport(handler), clock=clock,
    )


@pytest.mark.parametrize("source_type", ["fake_gateway", "external_api"])
@pytest.mark.parametrize("empty", [False, True])
def test_source_and_observation_time_are_preserved_for_results_and_no_match(source_type, empty):
    async def scenario():
        payload = _fixture("empty.json" if empty else "success.json")
        payload["source"] = {"source_type": "forged", "source_name": "private-provider-canary"}
        gateway = (
            _postal(lambda request: httpx.Response(200, json=payload))
            if source_type == "external_api"
            else FakeTrackingGateway({} if empty else {MAIL: _data()}, clock=lambda: NOW)
        )
        try:
            result = await TrackingTool(gateway).execute(TrackingCommand(mail_no=MAIL))
            assert result.status.value == ("no_match" if empty else "success")
            assert result.provenance[0].source_type == source_type
            assert result.provenance[0].queried_at == NOW
            assert bool(result.data) is not empty
            assert "最新轨迹" not in result.answer
            assert result.warnings
            if source_type == "external_api":
                assert result.provenance[0].source_profile == "postal-tracking-doc-2019-v1"
                assert "fake_gateway" not in result.model_dump_json()
            else:
                assert any("合成测试数据" in warning for warning in result.warnings)
            assert "private-provider-canary" not in result.model_dump_json()
            if empty:
                assert "不代表邮件不存在" in result.answer
        finally:
            if source_type == "external_api":
                await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("completeness", ["complete", "partial", "unknown"])
def test_tool_does_not_invent_history_completeness(completeness):
    class Gateway:
        async def query(self, command):
            return TrackingQueryResult(
                data=_data(), queried_at=NOW, history_completeness=completeness,
                source=TrackingSource(source_type="external_api", source_name="synthetic", profile="fixture-v1"),
            )

    result = asyncio.run(TrackingTool(Gateway()).execute(TrackingCommand(mail_no=MAIL)))
    assert result.status.value == ("partial" if completeness == "partial" else "success")
    assert bool(result.warnings) is (completeness != "complete")


@pytest.mark.parametrize("bare_result", [None, _data()])
def test_missing_observation_contract_is_not_silently_labelled_fake(bare_result):
    class Gateway:
        async def query(self, command):
            return bare_result

    with pytest.raises(AgentOperationError) as error:
        asyncio.run(TrackingTool(Gateway()).execute(TrackingCommand(mail_no=MAIL)))
    assert error.value.failure.code == "tracking_observation_missing"


@pytest.mark.parametrize(
    "changes", [{"source_type": "unknown"}, {"source_name": "https://private.example.test"}, {"profile": "key=secret"}],
)
def test_source_metadata_is_a_bounded_internal_contract(changes):
    with pytest.raises(ValidationError):
        TrackingSource(**{
            "source_type": "external_api", "source_name": "synthetic", "profile": "fixture-v1", **changes,
        })


@pytest.mark.parametrize("queried_at", [NOW.replace(tzinfo=None), NOW + timedelta(seconds=1)])
def test_observation_and_data_time_must_agree(queried_at):
    with pytest.raises(ValidationError):
        TrackingQueryResult(
            data=_data(), queried_at=queried_at,
            source=TrackingSource(source_type="external_api", source_name="synthetic", profile="fixture-v1"),
        )


@pytest.mark.parametrize("initial_empty", [False, True])
def test_postal_mock_graph_sqlite_and_v2_keep_freshness_separate_from_http_replay(tmp_path, initial_empty):
    calls = []
    current = [NOW]

    def handler(request):
        calls.append(parse_qs(request.content.decode()))
        payload = _fixture("empty.json" if initial_empty and len(calls) == 1 else "success.json")
        if len(calls) > 1:
            payload["responseItems"][0].update(opName="妥投", opDesc="合成妥投事件", opTime="2026-09-09 10:01:00")
        return httpx.Response(200, json=payload)

    async def scenario():
        gateway = _postal(handler, lambda: current[0])
        try:
            async with create_persistent_agent(database_path=tmp_path / "postal-v2.db", tracking_gateway=gateway, clock=lambda: current[0]) as components:
                app = create_app(
                    settings=AssistantSettings(auth_enabled=True, api_keys="synthetic-client", rate_limit_enabled=False),
                    agent_api=AgentApiDependencies(service=components.service, capabilities=components.runtime.capability_descriptors),
                )
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://assistant.test") as client:
                    headers = {"Authorization": "Bearer synthetic-client", "Idempotency-Key": "synthetic-query-1"}
                    body = {"message": f"查邮件 {MAIL}"}
                    first = await client.post("/v2/agent/messages", headers=headers, json=body)
                    assert first.status_code == 200
                    first_result = first.json()["result"]
                    assert first_result["status"] == ("no_match" if initial_empty else "success")
                    current[0] += timedelta(minutes=1)
                    replay = await client.post("/v2/agent/messages", headers=headers, json=body)
                    assert replay.status_code == 200 and replay.json()["result"] == first_result
                    assert len(calls) == 1
                    fresh = await client.post(
                        "/v2/agent/messages",
                        headers={**headers, "Idempotency-Key": "synthetic-query-2"},
                        json={"conversation_id": first.json()["conversation_id"], "message": f"再查邮件 {MAIL}"},
                    )
                    assert fresh.status_code == 200
                    result = fresh.json()["result"]
                    assert result["data"]["current_status"] == "妥投"
                    # T3 projects a bounded source DTO, not raw domain fields.
                    snapshot = await components.runtime.graph.aget_state(
                        components.runtime.config(first.json()["conversation_id"])
                    )
                    source = snapshot.values["result"]["provenance"][0]
                    assert source["source_type"] == "external_api"
                    assert source["source_profile"] == "postal-tracking-doc-2019-v1"
                    assert result["provenance"][0] == {
                        name: source[name] for name in (
                            "source_type", "source_name", "source_profile",
                            "queried_at", "history_completeness",
                        )
                    }
                    assert fresh.json()["warnings"]
                    assert datetime.fromisoformat(result["data"]["queried_at"]) == current[0]
                    assert len(calls) == 2 and calls[0]["serialNo"] != calls[1]["serialNo"]
                    for private in ("query_id", "legacy_tool_call", "signing_system_id", "dataDigest", "operatorName", "synthetic-system"):
                        assert private not in fresh.text
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["direct", "resume", "waiting"])
def test_failed_api_receipt_write_repairs_latest_durable_stop_without_new_tool_call(tmp_path, monkeypatch, case):
    database = tmp_path / "receipt-repair.db"
    original = SqliteConversationMetadataRepository.complete_idempotency
    failed = []
    key = "synthetic-message-key-never-in-graph"

    async def fail_once(self, **kwargs):
        if kwargs["key"] == key and not failed:
            failed.append(True)
            raise RuntimeError("synthetic receipt write failure")
        return await original(self, **kwargs)

    monkeypatch.setattr(SqliteConversationMetadataRepository, "complete_idempotency", fail_once)

    async def scenario():
        gateway = FakeTrackingGateway({MAIL: _data()}, clock=lambda: NOW)
        message = "查邮件" if case == "waiting" else MAIL if case == "resume" else f"查邮件 {MAIL}"
        async with create_persistent_agent(database_path=database, tracking_gateway=gateway, clock=lambda: NOW) as components:
            await components.service.create_conversation(owner_id="synthetic", conversation_id=THREAD)
            if case == "resume":
                await components.service.send_message(conversation_id=THREAD, owner_id="synthetic", idempotency_key="start", message="查邮件")
            with pytest.raises(RuntimeError, match="synthetic receipt write failure"):
                await components.service.send_message(conversation_id=THREAD, owner_id="synthetic", idempotency_key=key, message=message)
            before = await components.runtime.graph.aget_state(components.runtime.config(str(THREAD)))
            assert key not in json.dumps(before.values)
        previous_calls = len(gateway.commands)
        async with create_persistent_agent(database_path=database, tracking_gateway=gateway, clock=lambda: NOW) as restarted:
            normalized_equivalent_key = f" {key} " if case == "direct" else key
            repaired = await restarted.service.send_message(conversation_id=THREAD, owner_id="synthetic", idempotency_key=normalized_equivalent_key, message=message)
            replayed = await restarted.service.send_message(conversation_id=THREAD, owner_id="synthetic", idempotency_key=key, message=message)
            assert repaired == replayed
            assert repaired["phase"] == ("waiting_user" if case == "waiting" else "completed")
            assert repaired["turn_id"] == before.values["turn_id"]
            assert len(gateway.commands) == previous_calls == (0 if case == "waiting" else 1)
            after = await restarted.runtime.graph.aget_state(restarted.runtime.config(str(THREAD)))
            assert after.config == before.config

    asyncio.run(scenario())
