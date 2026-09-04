from __future__ import annotations

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import PostageCommand
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.ports import PostageGateway
from ..domain.results import (
    AgentResult,
    AgentResultStatus,
    SourceReference,
)
from ..domain.tooling import CommandModel, ToolDescriptor


POSTAGE_TOOL_NAME = "postage"
POSTAGE_DESCRIPTOR = ToolDescriptor(
    intent=Intent.POSTAGE,
    tool_name=POSTAGE_TOOL_NAME,
    command_type=PostageCommand,
    result_schema_name="PostageData",
    required_slots=("origin", "destination", "weight"),
    read_only=True,
    max_attempts=2,
    capability_version="phase-3a",
)


class PostageTool:
    """Typed domain tool; it never computes a price missing from the Gateway."""

    def __init__(
        self,
        gateway: PostageGateway,
        *,
        source_type: str = "gateway",
        source_name: str = "postage_gateway",
    ) -> None:
        if not source_type.strip() or not source_name.strip():
            raise ValueError("source_type 和 source_name 不能为空")
        self._gateway = gateway
        self._source_type = source_type.strip()
        self._source_name = source_name.strip()

    @property
    def descriptor(self) -> ToolDescriptor:
        return POSTAGE_DESCRIPTOR

    async def execute(self, command: CommandModel) -> AgentResult:
        if not isinstance(command, PostageCommand):
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.CONTRACT_VIOLATION,
                    code="postage_command_type_mismatch",
                    message="资费工具只接受 PostageCommand",
                )
            )

        data = await self._gateway.quote(command)
        if data is None:
            return AgentResult(
                tool=POSTAGE_TOOL_NAME,
                intent=Intent.POSTAGE,
                status=AgentResultStatus.NO_MATCH,
                answer="未查询到符合条件的资费报价。",
                reason_code="postage_quote_not_found",
            )
        return AgentResult(
            tool=POSTAGE_TOOL_NAME,
            intent=Intent.POSTAGE,
            status=AgentResultStatus.SUCCESS,
            answer="已查询到符合条件的资费报价。",
            data=data,
            provenance=[
                SourceReference(
                    source_type=self._source_type,
                    source_name=self._source_name,
                    queried_at=data.queried_at,
                )
            ],
        )
