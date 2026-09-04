from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from .commands import DeliveryTimeCommand, PostageCommand, TrackingCommand
from .conversations import (
    ConversationMetadata,
    ConversationStatus,
    IdempotencyClaim,
    IdempotencyReceipt,
)
from .device_price import DevicePriceRecord, DevicePriceSearchQuery
from .intents import Intent
from .models import ToolResult
from .policy import PolicyQueryResult
from .results import (
    AgentResult,
    DeliveryTimeData,
    PostageData,
    TrackingData,
)
from .tooling import CommandModel, ToolDescriptor, ToolExecutionReceipt
from .understanding import QueryUnderstandingResult


class AssistantTool(Protocol):
    @property
    def name(self) -> str: ...

    async def initialize(self) -> None: ...

    async def execute(self, question: str) -> ToolResult: ...

    def readiness(self) -> str: ...

    async def close(self) -> None: ...


class DevicePriceRepository(Protocol):
    async def initialize(self) -> None: ...

    async def search(
        self,
        query: DevicePriceSearchQuery,
    ) -> list[DevicePriceRecord]: ...

    def readiness(self) -> str: ...

    async def close(self) -> None: ...


class PolicyKnowledgeSource(Protocol):
    async def initialize(self) -> None: ...

    async def query(self, question: str) -> PolicyQueryResult: ...

    def readiness(self) -> str: ...

    async def close(self) -> None: ...


class TrackingGateway(Protocol):
    async def query(self, command: TrackingCommand) -> TrackingData | None: ...


class DeliveryTimeGateway(Protocol):
    async def query(
        self,
        command: DeliveryTimeCommand,
    ) -> DeliveryTimeData | None: ...


class PostageGateway(Protocol):
    async def quote(self, command: PostageCommand) -> PostageData | None: ...


class QueryUnderstander(Protocol):
    async def understand(
        self,
        *,
        message: str,
        active_intent: Intent | None = None,
        explicit_intent: Intent | None = None,
        expected_slots: tuple[str, ...] = (),
    ) -> QueryUnderstandingResult: ...


class StructuredQueryUnderstandingModel(Protocol):
    async def classify(
        self,
        *,
        message: str,
        prompt: str,
        prompt_version: str,
    ) -> Mapping[str, Any]: ...


class AgentTool(Protocol):
    @property
    def descriptor(self) -> ToolDescriptor: ...

    async def execute(self, command: CommandModel) -> AgentResult: ...


class ToolExecutionRepository(Protocol):
    async def find(
        self,
        *,
        conversation_id: str,
        argument_fingerprint: str,
    ) -> ToolExecutionReceipt | None: ...

    async def save(self, receipt: ToolExecutionReceipt) -> None: ...

    async def delete_conversation(self, conversation_id: str) -> int: ...


class ConversationMetadataRepository(Protocol):
    async def create(self, metadata: ConversationMetadata) -> None: ...

    async def create_idempotently(
        self,
        *,
        metadata: ConversationMetadata,
        key: str,
        request_hash: str,
    ) -> ConversationMetadata: ...

    async def get(
        self,
        conversation_id: UUID,
    ) -> ConversationMetadata | None: ...

    async def authorize(
        self,
        conversation_id: UUID,
        owner_id: str,
    ) -> bool: ...

    async def claim_idempotency(
        self,
        *,
        conversation_id: UUID,
        key: str,
        request_hash: str,
        now: datetime,
    ) -> IdempotencyClaim: ...

    async def complete_idempotency(
        self,
        *,
        conversation_id: UUID,
        key: str,
        request_hash: str,
        response: Mapping[str, Any],
        completed_at: datetime,
    ) -> IdempotencyReceipt: ...

    async def release_idempotency(
        self,
        *,
        conversation_id: UUID,
        key: str,
        request_hash: str,
    ) -> None: ...

    async def delete_idempotency_receipts(
        self,
        conversation_id: UUID,
    ) -> int: ...

    async def touch_expiry(
        self,
        *,
        conversation_id: UUID,
        expires_at: datetime,
        updated_at: datetime,
    ) -> None: ...

    async def set_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
        updated_at: datetime,
    ) -> None: ...

    async def list_expired(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[UUID]: ...

    async def delete(self, conversation_id: UUID) -> None: ...
