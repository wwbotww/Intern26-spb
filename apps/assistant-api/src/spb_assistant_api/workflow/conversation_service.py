from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4, uuid5

from ..domain.agent_errors import AgentOperationError
from ..domain.conversations import (
    ConversationMetadata,
    ConversationStatus,
    IdempotencyClaimStatus,
)
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.product_price_execution import PriceSelectionInput
from ..domain.ports import (
    ConversationMetadataRepository,
    ToolExecutionRepository,
)
from .migrations import AgentStateMigrator, CURRENT_AGENT_STATE_SCHEMA
from .runtime import StatefulAgentRuntime


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
    """Conversation lifecycle, idempotency and graph-run boundary."""

    def __init__(
        self,
        *,
        runtime: StatefulAgentRuntime,
        metadata: ConversationMetadataRepository,
        tool_receipts: ToolExecutionRepository | None = None,
        checkpointer: AsyncThreadCheckpointer | None = None,
        coordinator: ConversationRunCoordinator | None = None,
        ttl: timedelta = timedelta(minutes=30),
        clock: Callable[[], datetime] | None = None,
        migrator: AgentStateMigrator | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("conversation TTL 必须大于 0")
        self._runtime = runtime
        self._metadata = metadata
        self._tool_receipts = tool_receipts
        self._checkpointer = checkpointer
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
        resolved_id = conversation_id or uuid4()
        async with self._coordinator.claim(resolved_id):
            existing = await self._metadata.get(resolved_id)
            if existing is not None:
                if (
                    existing.owner_id != owner_id
                    or existing.status is not ConversationStatus.ACTIVE
                    or existing.expires_at <= now
                ):
                    raise _state_conflict(
                        "conversation_not_available",
                        "会话不存在或不可访问",
                    )
                return existing
            metadata = ConversationMetadata(
                conversation_id=resolved_id,
                owner_id=owner_id,
                created_at=now,
                updated_at=now,
                expires_at=now + self._ttl,
            )
            await self._metadata.create(metadata)
            return metadata

    async def create_conversation_idempotently(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ConversationMetadata:
        now = self._now()
        metadata = await self._metadata.create_idempotently(
            metadata=ConversationMetadata(
                conversation_id=uuid4(),
                owner_id=owner_id,
                created_at=now,
                updated_at=now,
                expires_at=now + self._ttl,
            ),
            key=idempotency_key,
            request_hash=request_hash,
        )
        if (
            metadata.owner_id != owner_id
            or metadata.status is not ConversationStatus.ACTIVE
            or metadata.expires_at <= now
        ):
            raise _state_conflict(
                "conversation_not_available",
                "会话不存在或不可访问",
            )
        return metadata

    async def send_message(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
        idempotency_key: str,
        message: str | None = None,
        explicit_intent: Intent | None = None,
        confirm_overwrite: bool = False,
        price_selection: PriceSelectionInput | None = None,
    ) -> Mapping[str, Any]:
        """Start a turn or resume an interrupt behind one stable API method."""

        payload = {
            "operation": "send_message",
            "message": message,
            "explicit_intent": (explicit_intent.value if explicit_intent else None),
            "confirm_overwrite": confirm_overwrite,
        }
        if price_selection is not None:
            payload["price_selection"] = price_selection.model_dump(mode="json")

        async def run(turn_id: UUID) -> Mapping[str, Any]:
            metadata = await self._metadata.get(conversation_id)
            assert (
                metadata is not None
            )  # Ownership/expiry checked under this run's lock.
            snapshot = await self._runtime.graph.aget_state(
                self._runtime.config(str(conversation_id))
            )
            if snapshot.values.get("phase") == "waiting_user":
                return await self._runtime.resume(
                    thread_id=str(conversation_id),
                    message=message,
                    selected_intent=explicit_intent,
                    confirm_overwrite=confirm_overwrite,
                    turn_id=turn_id,
                    owner_id=owner_id,
                    price_selection=price_selection,
                )
            if price_selection is not None:
                raise _state_conflict(
                    "price_selection_not_pending", "当前会话没有待选择的商品"
                )
            if message is None:
                raise AgentOperationError(
                    AgentFailure(
                        category=FailureCategory.INVALID_INPUT,
                        code="message_required_for_new_turn",
                        message="开始新一轮查询必须提供 message",
                    )
                )
            return await self._runtime.start(
                thread_id=str(conversation_id),
                message=message,
                explicit_intent=explicit_intent,
                turn_id=turn_id,
                owner_id=owner_id,
                session_expires_at=metadata.expires_at,
            )

        return await self._run_idempotently(
            conversation_id=conversation_id,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            request_hash=request_fingerprint(payload),
            operation=run,
            validate_checkpoint=True,
        )

    async def read_snapshot(
        self, *, conversation_id: UUID, owner_id: str
    ) -> Mapping[str, Any]:
        """Read the owned durable stop; never resume a node or extend a token TTL."""
        async with self._coordinator.claim(conversation_id):
            await self._require_active(
                conversation_id=conversation_id, owner_id=owner_id, now=self._now()
            )
            snapshot = await self._runtime.graph.aget_state(
                self._runtime.config(str(conversation_id))
            )
            if not snapshot.values:
                raise _state_conflict(
                    "conversation_not_available", "会话没有可恢复的内容"
                )
            self._migrator.migrate(snapshot.values)
            if snapshot.values.get("phase") not in {
                "waiting_user",
                "completed",
                "failed",
                "handoff",
            }:
                raise _state_conflict(
                    "conversation_run_in_progress", "查询尚未到达可恢复的状态"
                )
            return project_agent_output(snapshot.values)

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
            "explicit_intent": (explicit_intent.value if explicit_intent else None),
        }
        return await self._run_idempotently(
            conversation_id=conversation_id,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            request_hash=request_fingerprint(payload),
            operation=lambda turn_id: self._runtime.start(
                thread_id=str(conversation_id),
                message=message,
                explicit_intent=explicit_intent,
                turn_id=turn_id,
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
            "selected_intent": (selected_intent.value if selected_intent else None),
            "confirm_overwrite": confirm_overwrite,
        }
        return await self._run_idempotently(
            conversation_id=conversation_id,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            request_hash=request_fingerprint(payload),
            operation=lambda turn_id: self._runtime.resume(
                thread_id=str(conversation_id),
                message=message,
                selected_intent=selected_intent,
                confirm_overwrite=confirm_overwrite,
                turn_id=turn_id,
            ),
            validate_checkpoint=True,
        )

    async def delete_conversation(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
    ) -> None:
        """Idempotently remove workflow state while retaining a tombstone."""

        if self._tool_receipts is None or self._checkpointer is None:
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.PERSISTENCE_UNAVAILABLE,
                    code="conversation_deletion_not_configured",
                    message="会话清理依赖尚未配置",
                    retryable=True,
                )
            )
        async with self._coordinator.claim(conversation_id):
            metadata = await self._metadata.get(conversation_id)
            if metadata is None or metadata.owner_id != owner_id:
                raise _state_conflict(
                    "conversation_not_available",
                    "会话不存在或不可访问",
                )
            if metadata.status is ConversationStatus.DELETED:
                return
            try:
                await self._checkpointer.adelete_thread(str(conversation_id))
                await self._metadata.delete_idempotency_receipts(conversation_id)
                await self._tool_receipts.delete_conversation(str(conversation_id))
                await self._metadata.set_status(
                    conversation_id=conversation_id,
                    status=ConversationStatus.DELETED,
                    updated_at=self._now(),
                )
            except AgentOperationError:
                raise
            except Exception as error:
                raise AgentOperationError(
                    AgentFailure(
                        category=FailureCategory.PERSISTENCE_UNAVAILABLE,
                        code="conversation_deletion_failed",
                        message="会话状态未能完整清理",
                        retryable=True,
                    )
                ) from error

    async def _run_idempotently(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
        idempotency_key: str,
        request_hash: str,
        operation: Callable[[UUID], Awaitable[Mapping[str, Any]]],
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
                # Bind retryable API execution to a stable message identity.
                # The key is never written into graph state or telemetry.
                turn_id = uuid5(
                    conversation_id,
                    f"agent-message-v3:{claim.receipt.key}:{request_hash}",
                )
                snapshot = await self._runtime.graph.aget_state(
                    self._runtime.config(str(conversation_id))
                )
                if validate_checkpoint and snapshot.values:
                    self._migrator.migrate(snapshot.values)
                phase = snapshot.values.get("phase")
                stopped = (
                    not snapshot.next and phase in {"completed", "failed", "handoff"}
                ) or (
                    phase == "waiting_user"
                    and any(task.interrupts for task in snapshot.tasks)
                )
                if snapshot.values.get("turn_id") == str(turn_id) and stopped:
                    # Repair a failed API-receipt write after a durable stop.
                    # This is not a claim of cross-system exactly-once billing.
                    result = snapshot.values
                else:
                    result = await operation(turn_id)
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
                    state_schema_version=CURRENT_AGENT_STATE_SCHEMA,
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
        if metadata.status is ConversationStatus.DELETED:
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
        batch_size: int = 100,
    ) -> None:
        if batch_size < 1:
            raise ValueError("janitor batch_size 必须大于 0")
        self._metadata = metadata
        self._tool_receipts = tool_receipts
        self._checkpointer = checkpointer
        self._coordinator = coordinator or ConversationRunCoordinator()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._batch_size = batch_size

    async def cleanup_expired(self) -> CleanupResult:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock 必须返回包含时区的 datetime")
        conversation_ids = await self._metadata.list_expired(
            now=now,
            limit=self._batch_size,
        )
        completed = 0
        deleted_idempotency = 0
        deleted_receipts = 0
        failures: list[str] = []
        for conversation_id in conversation_ids:
            try:
                async with self._coordinator.claim(conversation_id):
                    await self._checkpointer.adelete_thread(str(conversation_id))
                    deleted_idempotency += (
                        await self._metadata.delete_idempotency_receipts(
                            conversation_id
                        )
                    )
                    deleted_receipts += await self._tool_receipts.delete_conversation(
                        str(conversation_id)
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
    projected = {name: result.get(name) for name in public_fields if name in result}
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
