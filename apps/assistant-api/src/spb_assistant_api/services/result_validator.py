from __future__ import annotations

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import TrackingCommand
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.results import (
    AgentResult,
    AgentResultStatus,
    TrackingData,
)
from ..domain.tooling import CommandModel


def _violation(code: str, message: str) -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(
            category=FailureCategory.CONTRACT_VIOLATION,
            code=code,
            message=message,
        )
    )


class AgentResultValidator:
    def validate(
        self,
        *,
        command: CommandModel,
        result: AgentResult,
    ) -> AgentResult:
        if result.intent.value != command.intent:
            raise _violation(
                "result_command_intent_mismatch",
                "结果意图与执行命令不一致",
            )
        if isinstance(command, TrackingCommand):
            self._validate_tracking(command, result)
        return result

    @staticmethod
    def _validate_tracking(
        command: TrackingCommand,
        result: AgentResult,
    ) -> None:
        if result.status in {
            AgentResultStatus.SUCCESS,
            AgentResultStatus.PARTIAL,
        }:
            if not isinstance(result.data, TrackingData):
                raise _violation(
                    "tracking_data_missing",
                    "轨迹成功结果必须包含类型化数据",
                )
            if result.data.mail_no != command.mail_no:
                raise _violation(
                    "tracking_mail_number_mismatch",
                    "轨迹结果邮件号与请求不一致",
                )
            occurred_at = [item.occurred_at for item in result.data.events]
            if any(value.tzinfo is None for value in occurred_at):
                raise _violation(
                    "tracking_event_time_without_timezone",
                    "轨迹节点时间必须包含时区",
                )
            if occurred_at != sorted(occurred_at):
                raise _violation(
                    "tracking_events_not_chronological",
                    "轨迹节点必须按时间正序返回",
                )
            if result.data.queried_at.tzinfo is None:
                raise _violation(
                    "tracking_query_time_without_timezone",
                    "轨迹查询时间必须包含时区",
                )
