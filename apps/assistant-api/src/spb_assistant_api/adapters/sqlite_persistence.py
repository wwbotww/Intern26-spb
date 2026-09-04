from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import aiosqlite

from ..domain.agent_errors import AgentOperationError
from ..domain.conversations import (
    ConversationMetadata,
    ConversationStatus,
    IdempotencyClaim,
    IdempotencyClaimStatus,
    IdempotencyReceipt,
    IdempotencyStatus,
)
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.tooling import ToolExecutionReceipt


_SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS agent_conversations (
    conversation_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    state_schema_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_idempotency_receipts (
    conversation_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    response_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (conversation_id, idempotency_key),
    FOREIGN KEY (conversation_id)
        REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_tool_execution_receipts (
    conversation_id TEXT NOT NULL,
    argument_fingerprint TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, argument_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_agent_conversations_expiry
    ON agent_conversations(status, expires_at);
"""


class SqliteConversationMetadataRepository:
    def __init__(
        self,
        connection: aiosqlite.Connection,
        lock: asyncio.Lock,
    ) -> None:
        self._connection = connection
        self._lock = lock

    async def create(self, metadata: ConversationMetadata) -> None:
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._fetch_metadata(metadata.conversation_id)
                if existing is not None:
                    if existing != metadata:
                        raise _conflict(
                            "conversation_metadata_conflict",
                            "会话标识已绑定到不同元数据",
                        )
                    await self._connection.commit()
                    return
                await self._connection.execute(
                    """
                    INSERT INTO agent_conversations (
                        conversation_id, owner_id, status,
                        state_schema_version, created_at, updated_at,
                        expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(metadata.conversation_id),
                        metadata.owner_id,
                        metadata.status.value,
                        metadata.state_schema_version,
                        _iso(metadata.created_at),
                        _iso(metadata.updated_at),
                        _iso(metadata.expires_at),
                    ),
                )
                await self._connection.commit()
            except BaseException:
                await self._connection.rollback()
                raise

    async def get(
        self,
        conversation_id: UUID,
    ) -> ConversationMetadata | None:
        async with self._lock:
            return await self._fetch_metadata(conversation_id)

    async def authorize(
        self,
        conversation_id: UUID,
        owner_id: str,
    ) -> bool:
        metadata = await self.get(conversation_id)
        return (
            metadata is not None
            and metadata.owner_id == owner_id
            and metadata.status is not ConversationStatus.DELETED
        )

    async def claim_idempotency(
        self,
        *,
        conversation_id: UUID,
        key: str,
        request_hash: str,
        now: datetime,
    ) -> IdempotencyClaim:
        normalized_key = _validate_key(key)
        _validate_hash(request_hash)
        _require_aware(now, "now")
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetch_idempotency_row(
                    conversation_id,
                    normalized_key,
                )
                if row is None:
                    await self._connection.execute(
                        """
                        INSERT INTO agent_idempotency_receipts (
                            conversation_id, idempotency_key, request_hash,
                            status, response_json, created_at, completed_at
                        ) VALUES (?, ?, ?, ?, NULL, ?, NULL)
                        """,
                        (
                            str(conversation_id),
                            normalized_key,
                            request_hash,
                            IdempotencyStatus.IN_PROGRESS.value,
                            _iso(now),
                        ),
                    )
                    receipt = IdempotencyReceipt(
                        conversation_id=conversation_id,
                        key=normalized_key,
                        request_hash=request_hash,
                        status=IdempotencyStatus.IN_PROGRESS,
                        created_at=now,
                    )
                    status = IdempotencyClaimStatus.CLAIMED
                else:
                    receipt = _idempotency_from_row(row)
                    if receipt.request_hash != request_hash:
                        status = IdempotencyClaimStatus.CONFLICT
                    elif receipt.status is IdempotencyStatus.COMPLETED:
                        status = IdempotencyClaimStatus.REPLAY
                    else:
                        status = IdempotencyClaimStatus.IN_PROGRESS
                await self._connection.commit()
                return IdempotencyClaim(status=status, receipt=receipt)
            except BaseException:
                await self._connection.rollback()
                raise

    async def complete_idempotency(
        self,
        *,
        conversation_id: UUID,
        key: str,
        request_hash: str,
        response: Mapping[str, Any],
        completed_at: datetime,
    ) -> IdempotencyReceipt:
        normalized_key = _validate_key(key)
        _validate_hash(request_hash)
        _require_aware(completed_at, "completed_at")
        response_value = json.loads(
            json.dumps(
                dict(response),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        encoded = json.dumps(
            response_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetch_idempotency_row(
                    conversation_id,
                    normalized_key,
                )
                if row is None:
                    raise _conflict(
                        "idempotency_claim_missing",
                        "完成幂等请求前必须先取得 claim",
                    )
                existing = _idempotency_from_row(row)
                if existing.request_hash != request_hash:
                    raise _conflict(
                        "idempotency_request_hash_conflict",
                        "相同幂等键对应了不同请求",
                    )
                if existing.status is IdempotencyStatus.COMPLETED:
                    await self._connection.commit()
                    return existing
                await self._connection.execute(
                    """
                    UPDATE agent_idempotency_receipts
                    SET status = ?, response_json = ?, completed_at = ?
                    WHERE conversation_id = ? AND idempotency_key = ?
                    """,
                    (
                        IdempotencyStatus.COMPLETED.value,
                        encoded,
                        _iso(completed_at),
                        str(conversation_id),
                        normalized_key,
                    ),
                )
                await self._connection.commit()
                return IdempotencyReceipt(
                    conversation_id=conversation_id,
                    key=normalized_key,
                    request_hash=request_hash,
                    status=IdempotencyStatus.COMPLETED,
                    response=response_value,
                    created_at=existing.created_at,
                    completed_at=completed_at,
                )
            except BaseException:
                await self._connection.rollback()
                raise

    async def release_idempotency(
        self,
        *,
        conversation_id: UUID,
        key: str,
        request_hash: str,
    ) -> None:
        normalized_key = _validate_key(key)
        _validate_hash(request_hash)
        async with self._lock:
            await self._connection.execute(
                """
                DELETE FROM agent_idempotency_receipts
                WHERE conversation_id = ? AND idempotency_key = ?
                  AND request_hash = ? AND status = ?
                """,
                (
                    str(conversation_id),
                    normalized_key,
                    request_hash,
                    IdempotencyStatus.IN_PROGRESS.value,
                ),
            )
            await self._connection.commit()

    async def delete_idempotency_receipts(
        self,
        conversation_id: UUID,
    ) -> int:
        async with self._lock:
            cursor = await self._connection.execute(
                """
                DELETE FROM agent_idempotency_receipts
                WHERE conversation_id = ?
                """,
                (str(conversation_id),),
            )
            await self._connection.commit()
            return max(cursor.rowcount, 0)

    async def touch_expiry(
        self,
        *,
        conversation_id: UUID,
        expires_at: datetime,
        updated_at: datetime,
    ) -> None:
        _require_aware(expires_at, "expires_at")
        _require_aware(updated_at, "updated_at")
        async with self._lock:
            cursor = await self._connection.execute(
                """
                UPDATE agent_conversations
                SET expires_at = ?, updated_at = ?
                WHERE conversation_id = ? AND status = ?
                """,
                (
                    _iso(expires_at),
                    _iso(updated_at),
                    str(conversation_id),
                    ConversationStatus.ACTIVE.value,
                ),
            )
            await self._connection.commit()
            if cursor.rowcount != 1:
                raise _conflict(
                    "conversation_not_active",
                    "只有活跃会话可以续期",
                )

    async def set_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
        updated_at: datetime,
    ) -> None:
        _require_aware(updated_at, "updated_at")
        async with self._lock:
            cursor = await self._connection.execute(
                """
                UPDATE agent_conversations SET status = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (status.value, _iso(updated_at), str(conversation_id)),
            )
            await self._connection.commit()
            if cursor.rowcount != 1:
                raise _conflict(
                    "conversation_not_found",
                    "会话不存在",
                )

    async def list_expired(self, *, now: datetime) -> list[UUID]:
        _require_aware(now, "now")
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT conversation_id FROM agent_conversations
                WHERE status IN (?, ?) AND expires_at <= ?
                ORDER BY expires_at, conversation_id
                """,
                (
                    ConversationStatus.ACTIVE.value,
                    ConversationStatus.EXPIRED.value,
                    _iso(now),
                ),
            )
            rows = await cursor.fetchall()
            return [UUID(str(row["conversation_id"])) for row in rows]

    async def delete(self, conversation_id: UUID) -> None:
        async with self._lock:
            await self._connection.execute(
                "DELETE FROM agent_conversations WHERE conversation_id = ?",
                (str(conversation_id),),
            )
            await self._connection.commit()

    async def _fetch_metadata(
        self,
        conversation_id: UUID,
    ) -> ConversationMetadata | None:
        cursor = await self._connection.execute(
            """
            SELECT conversation_id, owner_id, status, state_schema_version,
                   created_at, updated_at, expires_at
            FROM agent_conversations WHERE conversation_id = ?
            """,
            (str(conversation_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return ConversationMetadata(
            conversation_id=UUID(str(row["conversation_id"])),
            owner_id=str(row["owner_id"]),
            status=ConversationStatus(str(row["status"])),
            state_schema_version=str(row["state_schema_version"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            expires_at=datetime.fromisoformat(str(row["expires_at"])),
        )

    async def _fetch_idempotency_row(
        self,
        conversation_id: UUID,
        key: str,
    ) -> aiosqlite.Row | None:
        cursor = await self._connection.execute(
            """
            SELECT conversation_id, idempotency_key, request_hash, status,
                   response_json, created_at, completed_at
            FROM agent_idempotency_receipts
            WHERE conversation_id = ? AND idempotency_key = ?
            """,
            (str(conversation_id), key),
        )
        return await cursor.fetchone()


class SqliteToolExecutionRepository:
    def __init__(
        self,
        connection: aiosqlite.Connection,
        lock: asyncio.Lock,
    ) -> None:
        self._connection = connection
        self._lock = lock

    async def find(
        self,
        *,
        conversation_id: str,
        argument_fingerprint: str,
    ) -> ToolExecutionReceipt | None:
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT receipt_json FROM agent_tool_execution_receipts
                WHERE conversation_id = ? AND argument_fingerprint = ?
                """,
                (conversation_id, argument_fingerprint),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return ToolExecutionReceipt.model_validate_json(
                str(row["receipt_json"])
            )

    async def save(self, receipt: ToolExecutionReceipt) -> None:
        encoded = receipt.model_dump_json()
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self._connection.execute(
                    """
                    SELECT receipt_json FROM agent_tool_execution_receipts
                    WHERE conversation_id = ? AND argument_fingerprint = ?
                    """,
                    (
                        receipt.conversation_id,
                        receipt.argument_fingerprint,
                    ),
                )
                row = await cursor.fetchone()
                if row is not None:
                    existing = ToolExecutionReceipt.model_validate_json(
                        str(row["receipt_json"])
                    )
                    if existing != receipt:
                        raise _conflict(
                            "tool_receipt_conflict",
                            "相同参数指纹已存在不同的执行收据",
                        )
                    await self._connection.commit()
                    return
                await self._connection.execute(
                    """
                    INSERT INTO agent_tool_execution_receipts (
                        conversation_id, argument_fingerprint,
                        receipt_json, completed_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        receipt.conversation_id,
                        receipt.argument_fingerprint,
                        encoded,
                        _iso(receipt.completed_at),
                    ),
                )
                await self._connection.commit()
            except BaseException:
                await self._connection.rollback()
                raise

    async def delete_conversation(self, conversation_id: str) -> int:
        async with self._lock:
            cursor = await self._connection.execute(
                """
                DELETE FROM agent_tool_execution_receipts
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            )
            await self._connection.commit()
            return max(cursor.rowcount, 0)


@dataclass(frozen=True, slots=True)
class SqliteAgentRepositories:
    metadata: SqliteConversationMetadataRepository
    tool_receipts: SqliteToolExecutionRepository


@asynccontextmanager
async def create_sqlite_agent_repositories(
    database_path: str | Path,
) -> AsyncIterator[SqliteAgentRepositories]:
    path = str(database_path)
    if not path.strip():
        raise ValueError("agent database path 不能为空")
    async with aiosqlite.connect(path) as connection:
        connection.row_factory = aiosqlite.Row
        await connection.executescript(_SCHEMA)
        await connection.commit()
        lock = asyncio.Lock()
        yield SqliteAgentRepositories(
            metadata=SqliteConversationMetadataRepository(connection, lock),
            tool_receipts=SqliteToolExecutionRepository(connection, lock),
        )


def _idempotency_from_row(row: aiosqlite.Row) -> IdempotencyReceipt:
    response_text = row["response_json"]
    return IdempotencyReceipt(
        conversation_id=UUID(str(row["conversation_id"])),
        key=str(row["idempotency_key"]),
        request_hash=str(row["request_hash"]),
        status=IdempotencyStatus(str(row["status"])),
        response=(
            json.loads(str(response_text))
            if response_text is not None
            else None
        ),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        completed_at=(
            datetime.fromisoformat(str(row["completed_at"]))
            if row["completed_at"] is not None
            else None
        ),
    )


def _validate_key(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("幂等键长度必须为 1..255")
    return normalized


def _validate_hash(value: str) -> None:
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("request_hash 必须是 sha256 指纹")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} 必须包含时区")


def _iso(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(UTC).isoformat()


def _conflict(code: str, message: str) -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(
            category=FailureCategory.STATE_CONFLICT,
            code=code,
            message=message,
        )
    )
