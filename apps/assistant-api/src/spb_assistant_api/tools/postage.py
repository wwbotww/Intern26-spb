from __future__ import annotations

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import PostageCommand
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.ports import PostageGateway
from ..domain.postage_observation import PostageQuoteObservation
from ..domain.results import (
    AgentResult,
    AgentResultStatus,
    SourceReference,
)
from ..domain.tooling import CommandModel, ToolDescriptor
from ..services.postage_preflight import PostagePreflight


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
        preflight: PostagePreflight | None = None,
    ) -> None:
        if not source_type.strip() or not source_name.strip():
            raise ValueError("source_type 和 source_name 不能为空")
        self._gateway = gateway
        self._source_type = source_type.strip()
        self._source_name = source_name.strip()
        self._preflight = preflight

    @property
    def descriptor(self) -> ToolDescriptor:
        if self._preflight is not None:
            return ToolDescriptor(
                intent=Intent.POSTAGE, tool_name=POSTAGE_TOOL_NAME,
                command_type=PostageCommand, result_schema_name="PostageData",
                required_slots=("origin", "destination", "weight", "product_code"),
                capability_version="phase-3b-p3" if self._preflight.require_confirmation else "phase-3b-p1",
            )
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

        if self._preflight is not None:
            self._preflight.validate_command(command)
        observed = await self._gateway.quote(command)
        if isinstance(observed, PostageQuoteObservation):
            try:
                observed = PostageQuoteObservation.model_validate(observed.model_dump())
                if (
                    self._preflight is not None
                    and self._preflight.catalog.evidence == "synthetic"
                    and observed.source.source_type != "fake_gateway"
                ):
                    raise ValueError("合成目录不能宣称为真实来源报价")
            except ValueError:
                raise AgentOperationError(AgentFailure(
                    category=FailureCategory.CONTRACT_VIOLATION,
                    code="postage_observation_invalid", message="报价观察不符合契约",
                )) from None
            return AgentResult(
                tool=POSTAGE_TOOL_NAME, intent=Intent.POSTAGE,
                status=AgentResultStatus.SUCCESS, answer="已取得指定产品和条件的资费试算，不是最终支付价。",
                data=observed.data,
                warnings=["这是合成演示报价，不可用于实际寄递。"]
                if observed.source.source_type == "fake_gateway" else ["试算以所选产品和输入条件为准，不是最终支付价。"],
                provenance=[SourceReference(
                    source_type=observed.source.source_type,
                    source_name=observed.source.source_name,
                    source_profile=observed.source.profile,
                    queried_at=observed.queried_at,
                )],
            )
        if self._preflight is not None:
            raise AgentOperationError(AgentFailure(
                category=FailureCategory.CONTRACT_VIOLATION,
                code="postage_observation_required", message="新询价路径必须返回明确的报价观察，不能用空值或旧报价代替",
            ))
        data = observed
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
