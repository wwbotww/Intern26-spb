from __future__ import annotations

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import DeliveryTimeCommand
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.ports import DeliveryTimeGateway
from ..domain.results import (
    AgentResult,
    AgentResultStatus,
    SourceReference,
)
from ..domain.tooling import CommandModel, ToolDescriptor


DELIVERY_TIME_TOOL_NAME = "delivery_time"
DELIVERY_TIME_DESCRIPTOR = ToolDescriptor(
    intent=Intent.DELIVERY_TIME,
    tool_name=DELIVERY_TIME_TOOL_NAME,
    command_type=DeliveryTimeCommand,
    result_schema_name="DeliveryTimeData",
    required_slots=("origin", "destination"),
    read_only=True,
    max_attempts=2,
    capability_version="phase-3a",
)


class DeliveryTimeTool:
    """Typed domain tool; wire-format mapping remains in its Gateway."""

    def __init__(
        self,
        gateway: DeliveryTimeGateway,
        *,
        source_type: str = "gateway",
        source_name: str = "delivery_time_gateway",
    ) -> None:
        if not source_type.strip() or not source_name.strip():
            raise ValueError("source_type 和 source_name 不能为空")
        self._gateway = gateway
        self._source_type = source_type.strip()
        self._source_name = source_name.strip()

    @property
    def descriptor(self) -> ToolDescriptor:
        return DELIVERY_TIME_DESCRIPTOR

    async def execute(self, command: CommandModel) -> AgentResult:
        if not isinstance(command, DeliveryTimeCommand):
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.CONTRACT_VIOLATION,
                    code="delivery_time_command_type_mismatch",
                    message="时限工具只接受 DeliveryTimeCommand",
                )
            )

        data = await self._gateway.query(command)
        if data is None:
            return AgentResult(
                tool=DELIVERY_TIME_TOOL_NAME,
                intent=Intent.DELIVERY_TIME,
                status=AgentResultStatus.NO_MATCH,
                answer="未查询到该路线的寄递时限。",
                reason_code="delivery_time_not_found",
            )
        return AgentResult(
            tool=DELIVERY_TIME_TOOL_NAME,
            intent=Intent.DELIVERY_TIME,
            status=AgentResultStatus.SUCCESS,
            answer="已查询到该路线的预计寄递时限。",
            data=data,
            provenance=[
                SourceReference(
                    source_type=self._source_type,
                    source_name=self._source_name,
                    queried_at=data.queried_at,
                )
            ],
        )
