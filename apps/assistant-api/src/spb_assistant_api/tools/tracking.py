from __future__ import annotations

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import TrackingCommand
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.ports import TrackingGateway
from ..domain.results import (
    AgentResult,
    AgentResultStatus,
    SourceReference,
)
from ..domain.tooling import CommandModel, ToolDescriptor


TRACKING_TOOL_NAME = "tracking"
TRACKING_DESCRIPTOR = ToolDescriptor(
    intent=Intent.TRACKING,
    tool_name=TRACKING_TOOL_NAME,
    command_type=TrackingCommand,
    result_schema_name="TrackingData",
    required_slots=("mail_no",),
    read_only=True,
    max_attempts=2,
    capability_version="phase-1",
)


class TrackingTool:
    def __init__(self, gateway: TrackingGateway) -> None:
        self._gateway = gateway

    @property
    def descriptor(self) -> ToolDescriptor:
        return TRACKING_DESCRIPTOR

    async def execute(self, command: CommandModel) -> AgentResult:
        if not isinstance(command, TrackingCommand):
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.CONTRACT_VIOLATION,
                    code="tracking_command_type_mismatch",
                    message="轨迹工具只接受 TrackingCommand",
                )
            )

        data = await self._gateway.query(command)
        if data is None:
            return AgentResult(
                tool=TRACKING_TOOL_NAME,
                intent=Intent.TRACKING,
                status=AgentResultStatus.NO_MATCH,
                answer="未查询到该邮件的轨迹记录。",
                reason_code="tracking_not_found",
            )
        return AgentResult(
            tool=TRACKING_TOOL_NAME,
            intent=Intent.TRACKING,
            status=AgentResultStatus.SUCCESS,
            answer="已查询到该邮件的最新轨迹。",
            data=data,
            provenance=[
                SourceReference(
                    source_type="fake_gateway",
                    source_name="phase-1-tracking-fixture",
                    queried_at=data.queried_at,
                )
            ],
        )
