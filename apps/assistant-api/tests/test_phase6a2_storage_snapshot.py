import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.storage_cli import main
from spb_assistant_api.storage_paths import DATABASE_NAME, STORE_MARKER, StorageError, init_store, store_lease
from spb_assistant_api.storage_snapshot import MANIFEST, SNAPSHOT, backup_store, restore_backup, verify_backup
from spb_assistant_api import storage_snapshot
from spb_assistant_api.workflow.composition import create_persistent_agent

from .browser_session_fixture import ORIGIN, app_at
from .test_phase6a1_browser_api import headers
from .postage_p3_fixture import CONFIRM, QUERY


def empty_store(tmp_path):
    store = init_store(tmp_path / "source")

    async def initialize():
        async with create_persistent_agent(database_path=store / DATABASE_NAME):
            pass

    asyncio.run(initialize())
    return store


def send(client, message, *, conversation=None, key="synthetic"):
    return client.post("/v2/agent/messages", json={"message": message, "conversation_id": conversation}, headers={"Idempotency-Key": key})


def test_whole_store_restores_owner_checkpoint_creation_message_and_tool_receipts(tmp_path):
    source = init_store(tmp_path / "source")
    transports = []
    with TestClient(app_at(source / DATABASE_NAME, transports), base_url=ORIGIN, headers=headers()) as client:
        identity = client.post("/v2/agent/browser-session", json={}).json()
        client.headers["X-Agent-Session"] = identity["session_ref"]
        first = send(client, QUERY, key="create-completed").json()
        completed = send(client, CONFIRM, conversation=first["conversation_id"], key="confirm").json()
        pending = send(client, QUERY, key="create-pending").json()
        assert completed["phase"] == "completed" and pending["phase"] == "waiting_user"
        cookies = client.cookies.jar
        with pytest.raises(StorageError, match="正在使用"):
            backup_store(source, tmp_path / "busy-backup")
        assert not (tmp_path / "busy-backup").exists()
    original = (source / DATABASE_NAME).read_bytes()
    manifest = backup_store(source, tmp_path / "backup")
    assert manifest["table_rows"]["agent_conversations"] == 2
    assert manifest["table_rows"]["agent_tool_execution_receipts_v2"] == 1
    assert manifest["table_rows"]["checkpoints"] > 2
    assert manifest["table_rows"]["writes"] > 0
    assert verify_backup(tmp_path / "backup") == manifest
    restore_backup(tmp_path / "backup", tmp_path / "restored")
    assert (source / DATABASE_NAME).read_bytes() == original
    assert json.loads((source / STORE_MARKER).read_text())["store_id"] != json.loads((tmp_path / "restored" / STORE_MARKER).read_text())["store_id"]
    assert hashlib.sha256((tmp_path / "restored" / DATABASE_NAME).read_bytes()).hexdigest() == manifest["sha256"]
    with TestClient(app_at(tmp_path / "restored" / DATABASE_NAME, transports), base_url=ORIGIN, headers=headers(), cookies=cookies) as client:
        assert client.post("/v2/agent/browser-session", json={}).json() == identity
        client.headers["X-Agent-Session"] = identity["session_ref"]
        creation_replay = send(client, QUERY, key="create-completed").json()
        assert creation_replay["conversation_id"] == first["conversation_id"]
        replay = send(client, CONFIRM, conversation=first["conversation_id"], key="confirm").json()
        assert replay["result"] == completed["result"]
        assert transports[-1].calls == []
        resumed = send(client, CONFIRM, conversation=pending["conversation_id"], key="finish-pending").json()
        assert resumed["phase"] == "completed" and len(transports[-1].calls) == 1
        assert client.delete(f"/v2/agent/conversations/{first['conversation_id']}").status_code == 204
        assert send(client, CONFIRM, conversation=first["conversation_id"], key="confirm").status_code == 404
        old_ref = client.headers["X-Agent-Session"]
        fresh = client.post("/v2/agent/browser-session", json={"reset": True}).json()
        assert fresh["session_ref"] != old_ref
        client.headers["X-Agent-Session"] = fresh["session_ref"]
        assert send(client, CONFIRM, conversation=pending["conversation_id"]).status_code == 404
    after_delete = backup_store(tmp_path / "restored", tmp_path / "after-delete")
    assert after_delete["table_rows"]["agent_tool_execution_receipts_v2"] == 1


def test_restore_keeps_absolute_ttl_and_cleanup_removes_expired_state(tmp_path):
    source = empty_store(tmp_path)
    now = datetime(2026, 9, 10, tzinfo=UTC)

    async def seed():
        async with create_persistent_agent(database_path=source / DATABASE_NAME, clock=lambda: now) as parts:
            return await parts.service.create_conversation(owner_id="synthetic-owner")

    metadata = asyncio.run(seed())
    backup_store(source, tmp_path / "backup")
    restore_backup(tmp_path / "backup", tmp_path / "restored")

    async def scenario():
        async with create_persistent_agent(database_path=tmp_path / "restored" / DATABASE_NAME, clock=lambda: now + timedelta(hours=1)) as parts:
            with pytest.raises(AgentOperationError) as failure:
                await parts.service.send_message(conversation_id=metadata.conversation_id, owner_id="synthetic-owner", message="合成查询", idempotency_key="expired")
            assert failure.value.failure.code == "conversation_expired"
            result = await parts.janitor.cleanup_expired()
            assert result.expired_conversations == 1 and not result.failures

    asyncio.run(scenario())


def test_committed_wal_is_included_and_bundle_needs_no_sidecars(tmp_path):
    source = empty_store(tmp_path)
    # A raw fixture connection simulates a retained committed WAL after a crash.
    connection = sqlite3.connect(source / DATABASE_NAME)
    connection.execute("PRAGMA wal_autocheckpoint=0")
    values = ("synthetic-wal-id", "synthetic-owner", "active", "3", "2026-09-10T00:00:00+00:00", "2026-09-10T00:00:00+00:00", "2026-09-10T01:00:00+00:00")
    connection.execute("INSERT INTO agent_conversations VALUES (?,?,?,?,?,?,?)", values)
    connection.commit()
    assert (source / (DATABASE_NAME + "-wal")).stat().st_size > 0
    try:
        result = backup_store(source, tmp_path / "backup")
    finally:
        connection.close()
    assert result["table_rows"]["agent_conversations"] == 1
    assert {p.name for p in (tmp_path / "backup").iterdir()} == {MANIFEST, SNAPSHOT}
    assert verify_backup(tmp_path / "backup")["table_rows"]["agent_conversations"] == 1


@pytest.mark.parametrize("mutation,code", [
    ("truncate", "digest_mismatch"), ("hash", "digest_mismatch"), ("rows", "manifest_mismatch"),
    ("version", "invalid_manifest"), ("runtime", "incompatible_runtime"), ("sidecar", "unsealed_backup"),
    ("permissions", "unsafe_permissions"), ("symlink", "unsafe_permissions"), ("bad_json", "invalid_manifest"),
])
def test_tampered_backup_rejected_before_creating_restore_target(tmp_path, mutation, code):
    source = empty_store(tmp_path)
    bundle = tmp_path / "backup"
    backup_store(source, bundle)
    manifest = json.loads((bundle / MANIFEST).read_text())
    if mutation == "truncate":
        with (bundle / SNAPSHOT).open("r+b") as output:
            output.truncate(1024)
    elif mutation == "permissions":
        (bundle / SNAPSHOT).chmod(0o644)
    elif mutation == "symlink":
        original = tmp_path / "original-snapshot"
        (bundle / SNAPSHOT).rename(original)
        (bundle / SNAPSHOT).symlink_to(original)
    elif mutation == "sidecar":
        (bundle / (SNAPSHOT + "-wal")).touch()
    elif mutation == "bad_json":
        (bundle / MANIFEST).write_text("{")
    else:
        if mutation == "hash":
            manifest["sha256"] = "0" * 64
        elif mutation == "rows":
            manifest["table_rows"]["checkpoints"] += 1
        elif mutation == "version":
            manifest["format_version"] = 2
        else:
            manifest["runtime_versions"]["langgraph"] = "future"
        (bundle / MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(StorageError) as failure:
        restore_backup(bundle, tmp_path / "not-created")
    assert failure.value.code == code
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("mutation,code", [
    ("in_progress", "unfinished_requests"), ("missing_table", "incompatible_schema"),
    ("marker", "incompatible_schema"), ("orphan", "orphaned_state"), ("state", "incompatible_state"),
])
def test_unsafe_source_is_not_advertised_as_a_valid_backup(tmp_path, mutation, code):
    source = empty_store(tmp_path)
    with sqlite3.connect(source / DATABASE_NAME) as connection:
        if mutation == "missing_table":
            connection.execute("DROP TABLE writes")
        elif mutation == "marker":
            connection.execute("DELETE FROM agent_persistence_migrations")
        elif mutation == "orphan":
            connection.execute("INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id) VALUES ('orphan','','id')")
        else:
            connection.execute("INSERT INTO agent_conversations VALUES ('synthetic','owner','active',?,'start','update','expiry')", ("4" if mutation == "state" else "3",))
            if mutation == "in_progress":
                connection.execute("INSERT INTO agent_idempotency_receipts VALUES ('synthetic','key','hash','in_progress',NULL,'start',NULL)")
    with pytest.raises(StorageError) as failure:
        backup_store(source, tmp_path / "not-created")
    assert failure.value.code == code
    assert not (tmp_path / "not-created").exists()


def test_existing_destinations_never_overwritten_and_size_budget_leaves_unpublished_output(tmp_path):
    source = empty_store(tmp_path)
    bundle = tmp_path / "backup"
    backup_store(source, bundle)
    before = (source / DATABASE_NAME).read_bytes()
    for target in (source, bundle, tmp_path):
        with pytest.raises(FileExistsError):
            restore_backup(bundle, target)
    with pytest.raises(FileExistsError):
        backup_store(source, bundle)
    with pytest.raises(StorageError, match="大小上限"):
        backup_store(source, tmp_path / "too-small", max_bytes=1024)
    assert not (tmp_path / "too-small" / MANIFEST).exists()
    assert (source / DATABASE_NAME).read_bytes() == before


def test_cli_does_not_read_dotenv_or_start_app_and_errors_do_not_echo_paths(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ASSISTANT_QUERY_MODEL_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TRACKING_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_API_KEYS", "sensitive-canary")
    source = empty_store(tmp_path)
    assert main(["backup", "--source", str(source), "--destination", str(tmp_path / "backup")]) == 0
    assert main(["verify", "--source", str(tmp_path / "backup")]) == 0
    assert main(["restore", "--source", str(tmp_path / "backup"), "--destination", str(source)]) == 2
    output = capsys.readouterr()
    assert json.loads(output.err)["code"] == "target_exists"
    assert str(tmp_path) not in output.out + output.err
    assert "sensitive-canary" not in output.out + output.err
    child = subprocess.run([sys.executable, "-m", "spb_assistant_api.storage_cli", "verify", "--source", str(tmp_path / "backup")], capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)["status"] == "ok"


def test_expired_budget_refuses_backup_before_creating_output(tmp_path, monkeypatch):
    source = empty_store(tmp_path)
    ticks = iter((0, 31))
    monkeypatch.setattr(storage_snapshot.time, "monotonic", lambda: next(ticks, 31))
    with pytest.raises(StorageError) as failure:
        backup_store(source, tmp_path / "not-created", timeout=30)
    assert failure.value.code == "storage_timeout"
    assert not (tmp_path / "not-created").exists()
    with store_lease(source):
        pass


def test_failed_restore_retains_private_unpublished_directory(tmp_path, monkeypatch):
    source = empty_store(tmp_path)
    bundle, destination = tmp_path / "backup", tmp_path / "partial"
    manifest = backup_store(source, bundle)
    original_digest = storage_snapshot._digest

    def fail_output_digest(path, deadline, max_bytes):
        if path == destination / DATABASE_NAME:
            raise StorageError("synthetic_io_failure", "合成输出校验故障")
        return original_digest(path, deadline, max_bytes)

    monkeypatch.setattr(storage_snapshot, "_digest", fail_output_digest)
    with pytest.raises(StorageError, match="合成输出"):
        restore_backup(bundle, destination)
    assert destination.is_dir() and destination.stat().st_mode & 0o777 == 0o700
    assert not (destination / STORE_MARKER).exists()
    assert all(item.stat().st_mode & 0o777 == 0o600 for item in destination.iterdir())
    with pytest.raises(FileNotFoundError):
        with store_lease(destination):
            pass
    assert verify_backup(bundle) == manifest
