from __future__ import annotations

from typing import Protocol

from .device_price import DevicePriceRecord, DevicePriceSearchQuery
from .models import ToolResult
from .policy import PolicyQueryResult
from .commands import TrackingCommand
from .results import AgentResult, TrackingData
from .tooling import CommandModel, ToolDescriptor, ToolExecutionReceipt
from .intents import Intent
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


class QueryUnderstander(Protocol):
    async def understand(
        self,
        *,
        message: str,
        active_intent: Intent | None = None,
        explicit_intent: Intent | None = None,
    ) -> QueryUnderstandingResult: ...


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
