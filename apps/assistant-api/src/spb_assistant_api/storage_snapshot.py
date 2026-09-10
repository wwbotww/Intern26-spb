"""Quiescent whole-database snapshots, with no app startup or checkpoint decoding.

Only operator-trusted managed stores/bundles are supported. SHA-256 detects
accidental changes, not an adversary who can rewrite both snapshot and manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import UUID

from .storage_paths import (
    DATABASE_NAME, StorageError, absolute_path, create_private_file, fsync_directory,
    private_directory, private_file, publish_store, read_json, reserve_store,
    store_lease, write_json,
)

SNAPSHOT = "snapshot.sqlite3"
MANIFEST = "backup.json"
PROFILE = "agent-state-v3-receipt-v2-snapshot-v1"
DEFAULT_MAX_BYTES = 256 * 1024 * 1024
_COLUMNS = {
    "agent_conversations": ("conversation_id owner_id status state_schema_version created_at updated_at expires_at", "conversation_id"),
    "agent_idempotency_receipts": ("conversation_id idempotency_key request_hash status response_json created_at completed_at", "conversation_id idempotency_key"),
    "agent_conversation_creation_receipts": ("owner_id idempotency_key request_hash conversation_id created_at", "owner_id idempotency_key"),
    "agent_tool_execution_receipts": ("conversation_id argument_fingerprint receipt_json completed_at", "conversation_id argument_fingerprint"),
    "agent_tool_execution_receipts_v2": ("conversation_id tool_call_id argument_fingerprint receipt_json completed_at", "conversation_id tool_call_id"),
    "agent_persistence_migrations": ("migration_id", "migration_id"),
    "checkpoints": ("thread_id checkpoint_ns checkpoint_id parent_checkpoint_id type checkpoint metadata", "thread_id checkpoint_ns checkpoint_id"),
    "writes": ("thread_id checkpoint_ns checkpoint_id task_id idx channel type value", "thread_id checkpoint_ns checkpoint_id task_id idx"),
}


def _versions() -> dict[str, str]:
    return {name: version(name) for name in ("langgraph", "langgraph-checkpoint", "langgraph-checkpoint-sqlite")}


def _deadline(timeout: float, max_bytes: int) -> float:
    if not 0 < timeout <= 120 or type(max_bytes) is not int or not 1024 <= max_bytes <= 1024**3:
        raise StorageError("invalid_limits", "操作时限须在 0～120 秒内，大小上限须在 1 KiB～1 GiB 内")
    return time.monotonic() + timeout


def _check_time(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise StorageError("storage_timeout", "存储操作超时；不完整输出不能用于恢复")


def _connect(path: Path, deadline: float, *, readonly: bool) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=0.1)
    connection.execute("PRAGMA trusted_schema=OFF")
    if readonly:
        connection.execute("PRAGMA query_only=ON")
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    return connection


def _inspect(connection: sqlite3.Connection, deadline: float) -> dict:
    _check_time(deadline)
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise StorageError("integrity_failed", "SQLite 完整性检查失败")
    objects = connection.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
    tables = {name for kind, name, _ in objects if kind == "table"}
    if tables != set(_COLUMNS) or any(kind not in {"table", "index"} for kind, _, _ in objects):
        raise StorageError("incompatible_schema", "数据库不符合当前受控 Agent 存储合同")
    for table, (columns, primary) in _COLUMNS.items():
        fields = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        names = [row[1] for row in fields]
        keys = [row[1] for row in sorted(fields, key=lambda row: row[5]) if row[5]]
        if names != columns.split() or keys != primary.split():
            raise StorageError("incompatible_schema", "数据库字段或主键不兼容")
        for row in fields:
            expected = "BLOB" if row[1] in {"checkpoint", "metadata", "value"} else "INTEGER" if row[1] == "idx" else "TEXT"
            if row[2].upper() != expected:
                raise StorageError("incompatible_schema", "数据库字段类型不兼容")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StorageError("integrity_failed", "数据库存在关联完整性问题")
    markers = connection.execute("SELECT migration_id FROM agent_persistence_migrations").fetchall()
    if markers != [("tool-execution-scope-v2",)]:
        raise StorageError("incompatible_schema", "持久化迁移标记不兼容")
    if connection.execute("SELECT 1 FROM agent_conversations WHERE state_schema_version NOT IN ('1','2','3') LIMIT 1").fetchone():
        raise StorageError("incompatible_state", "会话 State 版本不受支持")
    if connection.execute("SELECT 1 FROM agent_idempotency_receipts WHERE status != 'completed' OR response_json IS NULL OR completed_at IS NULL LIMIT 1").fetchone():
        raise StorageError("unfinished_requests", "存在未完成消息 claim；请先按恢复流程核对，不自动清除或重试")
    for table, key in (("checkpoints", "thread_id"), ("writes", "thread_id"), ("agent_tool_execution_receipts", "conversation_id"), ("agent_tool_execution_receipts_v2", "conversation_id")):
        orphan = connection.execute(f'SELECT 1 FROM "{table}" AS item LEFT JOIN agent_conversations AS c ON c.conversation_id=item."{key}" WHERE c.conversation_id IS NULL OR c.status = \'deleted\' LIMIT 1').fetchone()
        if orphan:
            raise StorageError("orphaned_state", "发现缺失 owner 元数据或已删除会话的残留状态")
    counts = {table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in _COLUMNS}
    _check_time(deadline)
    return {
        "table_rows": counts,
        "schema_sha256": hashlib.sha256(json.dumps(objects, separators=(",", ":")).encode()).hexdigest(),
    }


def _digest(path: Path, deadline: float, max_bytes: int) -> tuple[str, int]:
    private_file(path)
    info = path.stat()
    if not 0 < info.st_size <= max_bytes:
        raise StorageError("snapshot_size", "数据库为空或超过大小上限")
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise StorageError("snapshot_size", "数据库超过大小上限")
            _check_time(deadline)
            digest.update(chunk)
    return digest.hexdigest(), size


def _copy_database(source: Path, target: Path, deadline: float, max_bytes: int) -> None:
    def progress(status: int, remaining: int, total: int):
        _check_time(deadline)
        if total * page_size > max_bytes:
            raise StorageError("snapshot_size", "数据库超过大小上限")

    with closing(_connect(source, deadline, readonly=True)) as origin, closing(_connect(target, deadline, readonly=False)) as output:
        page_size = origin.execute("PRAGMA page_size").fetchone()[0]
        origin.backup(output, pages=128, progress=progress, sleep=0.01)
        # Bundle is one self-contained DB, not a copied main file missing its WAL.
        output.execute("PRAGMA journal_mode=DELETE")
        _inspect(output, deadline)
    with target.open("rb") as saved:
        os.fsync(saved.fileno())


def backup_store(directory: str | Path, destination: str | Path, *, timeout: float = 30, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    deadline = _deadline(timeout, max_bytes)
    destination = absolute_path(destination)
    with store_lease(directory) as (source, marker):
        with closing(_connect(source / DATABASE_NAME, deadline, readonly=True)) as connection:
            _inspect(connection, deadline)
        destination.mkdir(mode=0o700)
        create_private_file(destination / SNAPSHOT)
        _copy_database(source / DATABASE_NAME, destination / SNAPSHOT, deadline, max_bytes)
        digest, size = _digest(destination / SNAPSHOT, deadline, max_bytes)
        with closing(_connect(destination / SNAPSHOT, deadline, readonly=True)) as connection:
            inspection = _inspect(connection, deadline)
        manifest = {
            "format_version": 1, "profile": PROFILE, "snapshot": SNAPSHOT,
            "source_store_id": marker["store_id"], "created_at": datetime.now(UTC).isoformat(),
            "sha256": digest, "size_bytes": size, "runtime_versions": _versions(),
            **inspection,
        }
        write_json(destination / MANIFEST, manifest)
        fsync_directory(destination)
        fsync_directory(destination.parent)
        return manifest


def _verify(directory: Path, deadline: float, max_bytes: int) -> dict:
    private_directory(directory)
    manifest = read_json(directory / MANIFEST)
    required = {"format_version", "profile", "snapshot", "source_store_id", "created_at", "sha256", "size_bytes", "runtime_versions", "table_rows", "schema_sha256"}
    if set(manifest) != required or type(manifest["format_version"]) is not int or manifest["format_version"] != 1 or manifest["profile"] != PROFILE or manifest["snapshot"] != SNAPSHOT:
        raise StorageError("invalid_manifest", "备份清单版本或文件约定不兼容")
    if manifest["runtime_versions"] != _versions():
        raise StorageError("incompatible_runtime", "备份与当前 LangGraph/Checkpointer 版本不一致；请使用匹配版本演练")
    try:
        valid = (
            str(UUID(manifest["source_store_id"])) == manifest["source_store_id"]
            and datetime.fromisoformat(manifest["created_at"]).utcoffset() is not None
            and all(isinstance(manifest[key], str) and re.fullmatch(r"[0-9a-f]{64}", manifest[key]) for key in ("sha256", "schema_sha256"))
            and isinstance(manifest["table_rows"], dict) and set(manifest["table_rows"]) == set(_COLUMNS)
            and all(type(count) is int and count >= 0 for count in manifest["table_rows"].values())
        )
        if not valid:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise StorageError("invalid_manifest", "备份清单字段不符合合同") from None
    # Refuse sidecars, temporary files and surprising extra payloads in a sealed bundle.
    if {path.name for path in directory.iterdir()} != {SNAPSHOT, MANIFEST}:
        raise StorageError("unsealed_backup", "备份目录必须只包含清单和完整数据库，不可含 WAL 或额外文件")
    digest, size = _digest(directory / SNAPSHOT, deadline, max_bytes)
    if digest != manifest["sha256"] or type(manifest["size_bytes"]) is not int or size != manifest["size_bytes"]:
        raise StorageError("digest_mismatch", "备份摘要或大小不一致")
    with closing(_connect(directory / SNAPSHOT, deadline, readonly=True)) as connection:
        inspection = _inspect(connection, deadline)
    if any(inspection[key] != manifest[key] for key in inspection):
        raise StorageError("manifest_mismatch", "备份结构或计数与清单不符")
    return manifest


def verify_backup(directory: str | Path, *, timeout: float = 30, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    return _verify(absolute_path(directory), _deadline(timeout, max_bytes), max_bytes)


def restore_backup(directory: str | Path, destination: str | Path, *, timeout: float = 30, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    deadline = _deadline(timeout, max_bytes)
    directory, destination = absolute_path(directory), absolute_path(destination)
    manifest = _verify(directory, deadline, max_bytes)
    reserve_store(destination)
    # A sealed DELETE-journal snapshot is a complete file; copying it (not a live
    # WAL database) permits byte-for-byte verification of the restored artifact.
    with os.fdopen(os.open(directory / SNAPSHOT, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source, os.fdopen(os.open(destination / DATABASE_NAME, os.O_WRONLY | os.O_NOFOLLOW), "wb") as output:
        copied = 0
        while chunk := source.read(1024 * 1024):
            _check_time(deadline)
            copied += len(chunk)
            if copied > max_bytes:
                raise StorageError("snapshot_size", "恢复数据超过大小上限")
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if _digest(destination / DATABASE_NAME, deadline, max_bytes) != (manifest["sha256"], manifest["size_bytes"]):
        raise StorageError("digest_mismatch", "恢复库与备份摘要不符，输出未发布")
    with closing(_connect(destination / DATABASE_NAME, deadline, readonly=True)) as connection:
        inspection = _inspect(connection, deadline)
    if any(inspection[key] != manifest[key] for key in inspection):
        raise StorageError("manifest_mismatch", "恢复库与备份清单不符，输出未发布")
    publish_store(destination)
    return manifest
