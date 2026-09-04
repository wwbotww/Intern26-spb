from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from ..domain.agent_errors import AgentOperationError
from ..domain.conversations import (
    ConversationMetadata,
    ConversationStatus,
    IdempotencyClaimStatus,
)
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.ports import (
    ConversationMetadataRepository,
    ToolExecutionRepository,
)
from .migrations import AgentStateMigrator
from .runtime import TrackingAgentRuntime


class AsyncThreadCheckpointer(Protocol):
    async def adelete_thread(self, thread_id: str) -> None: ...


class ConversationRunCoordinator:
    """Fail-fast single-process serialization for the local SQLite runtime."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def claim(self, conversation_id: UUID) -> AsyncIterator[None]:
        key = str(conversation_id)
        async with self._guard:
            lock = self._locks.setdefault(key, asyncio.Lock())
            if lock.locked():
                raise _state_conflict(
                    "conversation_run_in_progress",
                    "同一会话已有正在执行的 Workflow Run",
                )
            await lock.acquire()
        try:
            yield
        finally:
            lock.release()
            async with self._guard:
                if not lock.locked():
                    self._locks.pop(key, None)


class StatefulAgentService:
    """Metadata, TTL, idempotency and graph-run boundary for phase 2."""

    def __init__(
        self,
        *,
        runtime: TrackingAgentRuntime,
        metadata: ConversationMetadataRepository,
        coordinator: ConversationRunCoordinator | None = None,
        ttl: timedelta = timedelta(minutes=30),
        clock: Callable[[], datetime] | None = None,
        migrator: AgentStateMigrator | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("conversation TTL 必须大于 0")
        self._runtime = runtime
        self._metadata = metadata
        self._coordinator = coordinator or ConversationRunCoordinator()
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._migrator = migrator or AgentStateMigrator()

    async def create_conversation(
        self,
        *,
        owner_id: str,
        conversation_id: UUID | None = None,
    ) -> ConversationMetadata:
        now = self._now()
        metadata = ConversationMetadata(
            conversation_id=conversation_id or uuid4(),
            owner_id=owner_id,
            created_at=now,
            updated_at=now,
            expires_at=now + self._ttl,
        )
        await self._metadata.create(metadata)
        return metadata

    async def start(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
        idempotency_key: str,
        message: str,
        explicit_intent: Intent | None = None,
    ) -> Mapping[str, Any]:
        payload = {
            "operation": "start",
            "message": message,
            "explicit_intent": (
                explicit_intent.value if explicit_intent else None
            ),
        }
        return await self._run_idempotently(
            conversation_id=conversation_id,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            request_hash=request_fingerprint(payload),
            operation=lambda: self._runtime.start(
                thread_id=str(conversation_id),
                message=message,
                explicit_intent=explicit_intent,
            ),
            validate_checkpoint=False,
        )

    async def resume(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
        idempotency_key: str,
        message: str | None = None,
        selected_intent: Intent | None = None,
        confirm_overwrite: bool = False,
    ) -> Mapping[str, Any]:
        payload = {
            "operation": "resume",
            "message": message,
            "selected_intent": (
                selected_intent.value if selected_intent else None
            ),
            "confirm_overwrite": confirm_overwrite,
        }
        return await self._run_idempotently(
            conversation_id=conversation_id,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            request_hash=request_fingerprint(payload),
            operation=lambda: self._runtime.resume(
                thread_id=str(conversation_id),
                message=message,
                selected_intent=selected_intent,
                confirm_overwrite=confirm_overwrite,
            ),
            validate_checkpoint=True,
        )

    async def _run_idempotently(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
        idempotency_key: str,
        request_hash: str,
        operation: Callable[[], Awaitable[Mapping[str, Any]]],
        validate_checkpoint: bool,
    ) -> Mapping[str, Any]:
        async with self._coordinator.claim(conversation_id):
            now = self._now()
            await self._require_active(
                conversation_id=conversation_id,
                owner_id=owner_id,
                now=now,
            )
            claim = await self._metadata.claim_idempotency(
                conversation_id=conversation_id,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
            )
            if claim.status is IdempotencyClaimStatus.REPLAY:
                assert claim.receipt.response is not None
                return claim.receipt.response
            if claim.status is IdempotencyClaimStatus.CONFLICT:
                raise _state_conflict(
                    "idempotency_request_hash_conflict",
                    "相同幂等键不能用于不同请求",
                )
            if claim.status is IdempotencyClaimStatus.IN_PROGRESS:
                raise _state_conflict(
                    "idempotency_request_in_progress",
                    "相同幂等请求仍在处理中",
                )

            try:
                if validate_checkpoint:
                    snapshot = await self._runtime.graph.aget_state(
                        self._runtime.config(str(conversation_id))
                    )
                    if snapshot.values:
                        self._migrator.migrate(snapshot.values)
                result = await operation()
                public_result = project_agent_output(result)
                await self._metadata.complete_idempotency(
                    conversation_id=conversation_id,
                    key=idempotency_key,
                    request_hash=request_hash,
                    response=public_result,
                    completed_at=self._now(),
                )
                refreshed = self._now()
                await self._metadata.touch_expiry(
                    conversation_id=conversation_id,
                    expires_at=refreshed + self._ttl,
                    updated_at=refreshed,
                )
                return public_result
            except BaseException:
                await asyncio.shield(
                    self._metadata.release_idempotency(
                        conversation_id=conversation_id,
                        key=idempotency_key,
                        request_hash=request_hash,
                    )
                )
                raise

    async def _require_active(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
        now: datetime,
    ) -> None:
        metadata = await self._metadata.get(conversation_id)
        if metadata is None or metadata.owner_id != owner_id:
            raise _state_conflict(
                "conversation_not_available",
                "会话不存在或不可访问",
            )
        if (
            metadata.status is not ConversationStatus.ACTIVE
            or metadata.expires_at <= now
        ):
            if metadata.status is ConversationStatus.ACTIVE:
                await self._metadata.set_status(
                    conversation_id=conversation_id,
                    status=ConversationStatus.EXPIRED,
                    updated_at=now,
                )
            raise _state_conflict(
                "conversation_expired",
                "会话已过期，请重新开始",
            )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("clock 必须返回包含时区的 datetime")
        return value


@dataclass(frozen=True, slots=True)
class CleanupResult:
    expired_conversations: int
    deleted_idempotency_receipts: int
    deleted_tool_receipts: int
    failures: tuple[str, ...] = ()


class ConversationJanitor:
    def __init__(
        self,
        *,
        metadata: ConversationMetadataRepository,
        tool_receipts: ToolExecutionRepository,
        checkpointer: AsyncThreadCheckpointer,
        coordinator: ConversationRunCoordinator | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._metadata = metadata
        self._tool_receipts = tool_receipts
        self._checkpointer = checkpointer
        self._coordinator = coordinator or ConversationRunCoordinator()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def cleanup_expired(self) -> CleanupResult:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock 必须返回包含时区的 datetime")
        conversation_ids = await self._metadata.list_expired(now=now)
        completed = 0
        deleted_idempotency = 0
        deleted_receipts = 0
        failures: list[str] = []
        for conversation_id in conversation_ids:
            try:
                async with self._coordinator.claim(conversation_id):
                    await self._checkpointer.adelete_thread(
                        str(conversation_id)
                    )
                    deleted_idempotency += (
                        await self._metadata.delete_idempotency_receipts(
                            conversation_id
                        )
                    )
                    deleted_receipts += (
                        await self._tool_receipts.delete_conversation(
                            str(conversation_id)
                        )
                    )
                    await self._metadata.set_status(
                        conversation_id=conversation_id,
                        status=ConversationStatus.DELETED,
                        updated_at=now,
                    )
                    completed += 1
            except Exception:
                failures.append(str(conversation_id))
        return CleanupResult(
            expired_conversations=completed,
            deleted_idempotency_receipts=deleted_idempotency,
            deleted_tool_receipts=deleted_receipts,
            failures=tuple(failures),
        )


def request_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def project_agent_output(result: Mapping[str, Any]) -> dict[str, Any]:
    public_fields = (
        "conversation_id",
        "turn_id",
        "phase",
        "active_intent",
        "reply",
        "required_inputs",
        "result",
        "failure",
        "warnings",
        "finish_reason",
    )
    projected = {
        name: result.get(name)
        for name in public_fields
        if name in result
    }
    return json.loads(
        json.dumps(
            projected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _state_conflict(code: str, message: str) -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(
            category=FailureCategory.STATE_CONFLICT,
            code=code,
            message=message,
        )
    )
