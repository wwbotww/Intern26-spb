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
from ..domain.tracking import TrackingQueryResult


TRACKING_TOOL_NAME = "tracking"
TRACKING_DESCRIPTOR = ToolDescriptor(
    intent=Intent.TRACKING,
    tool_name=TRACKING_TOOL_NAME,
    command_type=TrackingCommand,
    result_schema_name="TrackingData",
    required_slots=("mail_no",),
    read_only=True,
    max_attempts=2,
    capability_version="phase-3b-t3",
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

        observation = await self._gateway.query(command)
        if not isinstance(observation, TrackingQueryResult):
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.CONTRACT_VIOLATION,
                    code="tracking_observation_missing",
                    message="轨迹接口必须返回包含来源和查询时间的类型化结果",
                )
            )
        data = observation.data
        provenance = [
            SourceReference(
                source_type=observation.source.source_type,
                source_name=observation.source.source_name,
                source_profile=observation.source.profile,
                history_completeness=observation.history_completeness,
                queried_at=observation.queried_at,
            )
        ]
        warnings = []
        if observation.source.source_type == "fake_gateway":
            warnings.append("当前结果来自合成测试数据，不代表真实邮件状态。")
        if observation.history_completeness != "complete":
            warnings.append("查询来源未确认完整历史；返回记录不代表现实中的最终投递状态。")
        if data is None:
            return AgentResult(
                tool=TRACKING_TOOL_NAME,
                intent=Intent.TRACKING,
                status=AgentResultStatus.NO_MATCH,
                answer="本次查询未返回该邮件的轨迹记录，不代表邮件不存在。",
                reason_code="tracking_not_found",
                provenance=provenance,
                warnings=warnings,
            )
        return AgentResult(
            tool=TRACKING_TOOL_NAME,
            intent=Intent.TRACKING,
            status=(
                AgentResultStatus.PARTIAL
                if observation.history_completeness == "partial"
                else AgentResultStatus.SUCCESS
            ),
            answer="已取得本次查询返回的邮件轨迹，请结合节点时间查看。",
            data=data,
            provenance=provenance,
            warnings=warnings,
        )
