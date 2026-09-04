from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

from ..domain.conversations import ConversationMetadata
from ..domain.intents import Intent
from ..domain.tooling import ToolDescriptor


PUBLIC_AGENT_SLOTS = frozenset(
    {"question", "mail_no", "origin", "destination", "weight"}
)


class AgentConversationService(Protocol):
    """Application boundary consumed by the V2 HTTP adapter."""

    async def create_conversation_idempotently(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ConversationMetadata: ...

    async def send_message(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
        idempotency_key: str,
        message: str | None = None,
        explicit_intent: Intent | None = None,
        confirm_overwrite: bool = False,
    ) -> Mapping[str, Any]: ...

    async def delete_conversation(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
    ) -> None: ...


class AgentReadinessProbe(Protocol):
    """Return only stable component names and coarse readiness states."""

    async def check(self) -> Mapping[str, str]: ...


class AgentCleanupResult(Protocol):
    expired_conversations: int
    deleted_idempotency_receipts: int
    deleted_tool_receipts: int
    failures: tuple[str, ...]


class AgentConversationJanitor(Protocol):
    async def cleanup_expired(self) -> AgentCleanupResult: ...


@dataclass(frozen=True, slots=True)
class AgentApiDependencies:
    """Dependencies required to opt the otherwise V1 app into V2 Agent API."""

    service: AgentConversationService
    capabilities: Mapping[Intent, ToolDescriptor]
    readiness_probe: AgentReadinessProbe | None = None
    janitor: AgentConversationJanitor | None = None
    run_timeout_seconds: float = 35.0
    janitor_interval_seconds: float = 300.0
    janitor_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.run_timeout_seconds <= 0:
            raise ValueError("run_timeout_seconds 必须大于 0")
        if self.janitor_interval_seconds <= 0:
            raise ValueError("janitor_interval_seconds 必须大于 0")
        if self.janitor_timeout_seconds <= 0:
            raise ValueError("janitor_timeout_seconds 必须大于 0")
        normalized = dict(self.capabilities)
        for intent, descriptor in normalized.items():
            if intent is Intent.UNKNOWN or descriptor.intent is not intent:
                raise ValueError("能力映射的 Intent 与 ToolDescriptor 不一致")
            unsupported_slots = (
                set(descriptor.required_slots) - PUBLIC_AGENT_SLOTS
            )
            if unsupported_slots:
                names = ", ".join(sorted(unsupported_slots))
                raise ValueError(f"V2 API 尚未声明以下必填槽位: {names}")
        object.__setattr__(
            self,
            "capabilities",
            MappingProxyType(normalized),
        )


AgentApiDependencyFactory = Callable[
    [],
    AbstractAsyncContextManager[AgentApiDependencies],
]
