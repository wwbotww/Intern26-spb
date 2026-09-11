from __future__ import annotations

import asyncio
import copy
import sqlite3
import traceback
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from spb_assistant_api.adapters.checkpointer_factory import create_sqlite_checkpointer
from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.adapters.in_memory_receipts import InMemoryToolExecutionRepository
from spb_assistant_api.adapters.sqlite_persistence import create_sqlite_agent_repositories
from spb_assistant_api.domain.agent_actions import InvokeToolAction
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.commands import TrackingCommand
from spb_assistant_api.domain.conversations import ConversationMetadata
from spb_assistant_api.domain.results import TrackingData, TrackingEvent
from spb_assistant_api.domain.tooling import ToolExecutionReceipt, argument_fingerprint
from spb_assistant_api.tools.tracking import TrackingTool
from spb_assistant_api.workflow.composition import create_agent_runtime, create_persistent_agent
from spb_assistant_api.workflow.migrations import AgentStateMigrator


NOW = datetime(2026, 9, 9, 2, tzinfo=UTC)
MAIL = "1234567890123"
THREAD = UUID("11111111-1111-4111-8111-111111111111")

# Actual legacy table shape, not the new module's CREATE TABLE script.
LEGACY_SCHEMA = """
CREATE TABLE agent_tool_execution_receipts (
    conversation_id TEXT NOT NULL, argument_fingerprint TEXT NOT NULL,
    receipt_json TEXT NOT NULL, completed_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, argument_fingerprint)
);
"""


def _data(status="运输中"):
    return TrackingData(
        mail_no=MAIL, current_status=status, queried_at=NOW,
        events=[TrackingEvent(description=f"合成{status}", occurred_at=NOW)],
    )


def _legacy_action():
    command = TrackingCommand(mail_no=MAIL)
    fingerprint = argument_fingerprint(command)
    return InvokeToolAction(
        tool_name="tracking", command=command,
        tool_call_id=uuid5(NAMESPACE_URL, f"{THREAD}:{fingerprint}"),
        argument_fingerprint=fingerprint, attempt=1,
        deadline_at=NOW + timedelta(seconds=30),
    )


async def _receipt():
    action = _legacy_action()
    result = await TrackingTool(FakeTrackingGateway({MAIL: _data()}, clock=lambda: NOW)).execute(action.command)
    result.provenance = [
        source.model_copy(update={"source_profile": ""}) for source in result.provenance
    ]
    return ToolExecutionReceipt(
        conversation_id=str(THREAD), tool_call_id=action.tool_call_id,
        tool_name=action.tool_name, argument_fingerprint=action.argument_fingerprint,
        result=result, completed_at=NOW,
    )


def _legacy_json(receipt):
    # Older SourceReference payloads have no source_profile field.
    return receipt.model_dump_json(exclude={
        "result": {"provenance": {"__all__": {"source_profile"}}}
    })


def _write_legacy(database, receipts):
    # Only pytest tmp_path databases are passed to this test helper.
    with sqlite3.connect(database) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.executemany(
            "INSERT INTO agent_tool_execution_receipts VALUES (?, ?, ?, ?)",
            [(r.conversation_id, r.argument_fingerprint, _legacy_json(r), r.completed_at.isoformat()) for r in receipts],
        )


@pytest.mark.parametrize("version", ["1", "2"])
def test_state_migration_preserves_known_pending_execution_without_mutation(version):
    action = _legacy_action()
    original = {
        "schema_version": version, "conversation_id": str(THREAD),
        "turn_id": str(UUID(int=1)), "pending_action": action.model_dump(mode="json"),
        "slots": {"intent": "tracking", "mail_no": MAIL},
        "tool_calls": [], "audit_events": [],
    }
    saved = copy.deepcopy(original)
    migrated = AgentStateMigrator().migrate(original)
    assert original == saved
    assert migrated.changed and migrated.target_version == "4"
    assert migrated.state["pending_action"] == saved["pending_action"]
    assert migrated.state["slots"] == saved["slots"]
    assert migrated.state["legacy_tool_call"]["tool_call_id"] == str(action.tool_call_id)
    assert "command" not in migrated.state["legacy_tool_call"]
    assert AgentStateMigrator().migrate(original).state == migrated.state
    assert not AgentStateMigrator().migrate(migrated.state).changed


@pytest.mark.parametrize("case", ["future", "missing_query", "bad_query", "forged_pending"])
def test_incompatible_state_or_legacy_identity_fails_closed(case):
    state = {"schema_version": "3", "conversation_id": str(THREAD)}
    if case == "future":
        state["schema_version"] = "999"
    elif case == "bad_query":
        state["query_id"] = "not-a-uuid"
    elif case == "forged_pending":
        state["schema_version"] = "2"
        state["pending_action"] = _legacy_action().model_dump(mode="json")
        state["pending_action"]["argument_fingerprint"] = "raw-private-canary"
    with pytest.raises(AgentOperationError) as error:
        AgentStateMigrator().migrate(state)
    assert error.value.failure.category.value == "state_schema_incompatible"
    assert "raw-private-canary" not in "".join(traceback.format_exception(error.value))


def test_migration_never_recovers_execution_identity_from_unscoped_history():
    state = {
        "schema_version": "2", "conversation_id": str(THREAD),
        "tool_calls": [{"tool_call_id": str(_legacy_action().tool_call_id)}],
    }
    migrated = AgentStateMigrator().migrate(state)
    assert migrated.state["legacy_tool_call"] is None


def test_new_execution_updates_legacy_metadata_version_without_changing_owner(tmp_path):
    database = tmp_path / "metadata-version.db"

    async def scenario():
        metadata = ConversationMetadata(
            conversation_id=THREAD, owner_id="synthetic-owner", state_schema_version="2",
            created_at=NOW, updated_at=NOW, expires_at=NOW + timedelta(minutes=30),
        )
        async with create_sqlite_agent_repositories(database) as repos:
            await repos.metadata.create(metadata)
        async with create_persistent_agent(
            database_path=database, tracking_gateway=FakeTrackingGateway(clock=lambda: NOW),
            clock=lambda: NOW,
        ) as components:
            await components.service.send_message(
                conversation_id=THREAD, owner_id="synthetic-owner", idempotency_key="query",
                message=f"查邮件 {MAIL}",
            )
        async with create_sqlite_agent_repositories(database) as repos:
            updated = await repos.metadata.get(THREAD)
            assert updated.state_schema_version == "4"
            assert updated.owner_id == metadata.owner_id
            assert updated.created_at == metadata.created_at

    asyncio.run(scenario())


def test_legacy_receipts_are_copied_once_and_deleted_from_both_tables(tmp_path):
    database = tmp_path / "legacy.db"

    async def scenario():
        receipt = await _receipt()
        _write_legacy(database, [receipt])
        for _ in range(2):
            async with create_sqlite_agent_repositories(database) as repos:
                assert await repos.tool_receipts.find(
                    conversation_id=str(THREAD), tool_call_id=receipt.tool_call_id
                ) == receipt
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM agent_tool_execution_receipts_v2").fetchone()[0] == 1
            assert connection.execute("SELECT receipt_json FROM agent_tool_execution_receipts").fetchone()[0] == _legacy_json(receipt)
        async with create_sqlite_agent_repositories(database) as repos:
            assert await repos.tool_receipts.delete_conversation(str(THREAD)) == 1
        # Reopening must not resurrect a deleted legacy record.
        async with create_sqlite_agent_repositories(database) as repos:
            assert await repos.tool_receipts.find(conversation_id=str(THREAD), tool_call_id=receipt.tool_call_id) is None
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM agent_tool_execution_receipts").fetchone()[0] == 0

    asyncio.run(scenario())


def test_readiness_requires_scope_migration_marker_and_correct_primary_key(tmp_path):
    database = tmp_path / "readiness.db"

    async def scenario():
        async with create_persistent_agent(database_path=database, clock=lambda: NOW) as components:
            assert (await components.readiness.check())["persistence"] == "ready"
            with sqlite3.connect(database) as connection:
                connection.execute("DELETE FROM agent_persistence_migrations")
            assert (await components.readiness.check())["persistence"] == "not_ready"
        # Invalid schema is rejected even if a marker claims it was migrated.
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO agent_persistence_migrations VALUES ('tool-execution-scope-v2')")
            connection.execute("ALTER TABLE agent_tool_execution_receipts_v2 RENAME TO synthetic_saved_receipts")
            connection.execute("CREATE TABLE agent_tool_execution_receipts_v2 (conversation_id TEXT NOT NULL, tool_call_id TEXT NOT NULL, argument_fingerprint TEXT NOT NULL, receipt_json TEXT NOT NULL, completed_at TEXT NOT NULL, PRIMARY KEY (conversation_id, argument_fingerprint))")
        with pytest.raises(AgentOperationError) as error:
            async with create_sqlite_agent_repositories(database):
                pytest.fail("wrong primary key must fail startup")
        assert error.value.failure.code == "tool_receipt_migration_failed"

    asyncio.run(scenario())


def test_restoring_after_old_validation_cannot_publish_invalid_facts(tmp_path):
    database = tmp_path / "old-validated.db"

    async def scenario():
        gateway = FakeTrackingGateway({MAIL: _data()}, clock=lambda: NOW)
        async with create_persistent_agent(database_path=database, tracking_gateway=gateway, clock=lambda: NOW) as components:
            runtime = components.runtime
            await runtime.start(thread_id=str(THREAD), message=f"查邮件 {MAIL}")
            snapshots = [s async for s in runtime.graph.aget_state_history(runtime.config(str(THREAD)))]
            before_response = next(s for s in snapshots if s.next == ("compose_response",))
            invalid = copy.deepcopy(before_response.values["last_result"])
            invalid["data"]["events"] = []
            legacy = await runtime.graph.aupdate_state(
                before_response.config,
                {"schema_version": "2", "query_id": None, "legacy_tool_call": None,
                 "pending_action": _legacy_action().model_dump(mode="json"), "last_result": invalid},
                as_node="validate_result",
            )
            output = await runtime.graph.ainvoke(None, config=legacy)
            assert output["phase"] == "failed" and output["result"] is None
            assert output["failure"]["code"] == "tracking_events_missing"
            assert len(gateway.commands) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["malformed_json", "column_mismatch", "identity_collision"])
def test_bad_legacy_data_rolls_back_all_copies_and_retains_original(tmp_path, case):
    database = tmp_path / "invalid-legacy.db"

    async def scenario():
        receipt = await _receipt()
        second = receipt.model_copy(update={"argument_fingerprint": "sha256:" + "b" * 64})
        _write_legacy(database, [receipt, second])
        with sqlite3.connect(database) as connection:
            if case == "malformed_json":
                connection.execute("UPDATE agent_tool_execution_receipts SET receipt_json = ? WHERE argument_fingerprint = ?", ("raw-private-canary", second.argument_fingerprint))
            elif case == "column_mismatch":
                connection.execute("UPDATE agent_tool_execution_receipts SET conversation_id = 'other' WHERE argument_fingerprint = ?", (second.argument_fingerprint,))
            original = connection.execute("SELECT * FROM agent_tool_execution_receipts ORDER BY argument_fingerprint").fetchall()
        with pytest.raises(AgentOperationError) as error:
            async with create_sqlite_agent_repositories(database):
                pytest.fail("invalid migration must not yield a ready repository")
        assert error.value.failure.code == "tool_receipt_migration_failed"
        assert "raw-private-canary" not in "".join(traceback.format_exception(error.value))
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT * FROM agent_tool_execution_receipts ORDER BY argument_fingerprint").fetchall() == original
            assert connection.execute("SELECT COUNT(*) FROM agent_tool_execution_receipts_v2").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM agent_persistence_migrations").fetchone()[0] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("cleanup", ["delete", "ttl"])
def test_conversation_cleanup_covers_migrated_and_new_receipts(tmp_path, cleanup):
    database = tmp_path / "cleanup.db"

    async def scenario():
        _write_legacy(database, [await _receipt()])
        current = [NOW]
        async with create_persistent_agent(
            database_path=database, tracking_gateway=FakeTrackingGateway({MAIL: _data()}, clock=lambda: current[0]),
            clock=lambda: current[0], conversation_ttl=timedelta(minutes=1),
        ) as components:
            await components.service.create_conversation(owner_id="synthetic", conversation_id=THREAD)
            await components.service.send_message(
                conversation_id=THREAD, owner_id="synthetic", idempotency_key="new-query",
                message=f"查邮件 {MAIL}",
            )
            if cleanup == "delete":
                await components.service.delete_conversation(conversation_id=THREAD, owner_id="synthetic")
            else:
                current[0] += timedelta(minutes=2)
                outcome = await components.janitor.cleanup_expired()
                assert outcome.deleted_tool_receipts == 2
            snapshot = await components.runtime.graph.aget_state(components.runtime.config(str(THREAD)))
            assert not snapshot.values
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM agent_tool_execution_receipts").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM agent_tool_execution_receipts_v2").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM agent_idempotency_receipts").fetchone()[0] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("checkpoint_kind", ["pending_tool", "waiting_user"])
def test_legacy_sqlite_checkpoint_restores_then_new_query_is_fresh(tmp_path, checkpoint_kind):
    database = tmp_path / "legacy-checkpoint.db"

    async def scenario():
        # Build a persisted JSON-native old-version checkpoint with unchanged
        # LangGraph node names. No production database or live gateway is used.
        async with create_sqlite_checkpointer(database) as saver:
            runtime = create_agent_runtime(
                checkpointer=saver, receipts=InMemoryToolExecutionRepository(),
                tracking_gateway=FakeTrackingGateway({MAIL: _data()}, clock=lambda: NOW),
                clock=lambda: NOW,
            )
            await runtime.start(thread_id=str(THREAD), message=f"查邮件 {MAIL}" if checkpoint_kind == "pending_tool" else "查邮件")
            history = [s async for s in runtime.graph.aget_state_history(runtime.config(str(THREAD)))]
            node = "execute_tool" if checkpoint_kind == "pending_tool" else "clarify"
            before = next(s for s in history if s.next == (node,))
            action = _legacy_action().model_dump(mode="json") if checkpoint_kind == "pending_tool" else before.values["pending_action"]
            legacy_config = await runtime.graph.aupdate_state(
                before.config,
                {"schema_version": "2", "query_id": None, "legacy_tool_call": None, "pending_action": action},
                as_node="decide_next",
            )
            if checkpoint_kind == "waiting_user":
                await runtime.graph.ainvoke(None, config=legacy_config)
        _write_legacy(database, [await _receipt()])
        gateway = FakeTrackingGateway({MAIL: _data("已签收")}, clock=lambda: NOW)
        async with create_persistent_agent(database_path=database, tracking_gateway=gateway, clock=lambda: NOW) as components:
            if checkpoint_kind == "pending_tool":
                replay = await components.runtime.graph.ainvoke(None, config=legacy_config)
                assert replay["result"]["data"]["current_status"] == "运输中"
                assert gateway.commands == []
            else:
                replay = await components.runtime.resume(thread_id=str(THREAD), message=MAIL)
                assert replay["result"]["data"]["current_status"] == "已签收"
                # An old conversation/argument receipt cannot prove the
                # current waiting query executed; do not reuse it.
                assert len(gateway.commands) == 1
            restored = await components.runtime.graph.aget_state(components.runtime.config(str(THREAD)))
            assert restored.values["schema_version"] == "4"
            old_query = restored.values["query_id"]
            new = await components.runtime.start(thread_id=str(THREAD), message=f"再查邮件 {MAIL}")
            latest = await components.runtime.graph.aget_state(components.runtime.config(str(THREAD)))
            assert new["result"]["data"]["current_status"] == "已签收"
            assert latest.values["query_id"] != old_query
            assert latest.values["legacy_tool_call"] is None
            assert len(gateway.commands) == (1 if checkpoint_kind == "pending_tool" else 2)

    asyncio.run(scenario())
