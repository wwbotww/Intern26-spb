from __future__ import annotations

from collections.abc import Callable

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import DevicePriceCommand, PolicyCommand
from ..domain.exceptions import (
    PriceRepositoryError,
    PolicySourceError,
    ToolContractError,
    ToolUnavailableError,
)
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.models import (
    DevicePriceEvidence,
    PolicyEvidence,
    QueryMode,
    ToolResult,
    ToolStatus,
)
from ..domain.ports import AssistantTool
from ..domain.results import (
    AgentResult,
    AgentResultStatus,
    DevicePriceData,
    DevicePriceEvidenceData,
    PolicyData,
    PolicyEvidenceData,
    SourceReference,
)
from ..domain.tooling import CommandModel, ToolDescriptor
from ..services.dispatcher import (
    DEVICE_PRICE_TOOL_NAME,
    POLICY_TOOL_NAME,
    validate_tool_result,
)


POLICY_COMPAT_DESCRIPTOR = ToolDescriptor(
    intent=Intent.POLICY,
    tool_name=POLICY_TOOL_NAME,
    command_type=PolicyCommand,
    result_schema_name="PolicyData",
    required_slots=("question",),
    read_only=True,
    max_attempts=2,
    capability_version="phase-4d-v1-compat",
)
DEVICE_PRICE_COMPAT_DESCRIPTOR = ToolDescriptor(
    intent=Intent.DEVICE_PRICE,
    tool_name=DEVICE_PRICE_TOOL_NAME,
    command_type=DevicePriceCommand,
    result_schema_name="DevicePriceData",
    required_slots=("question",),
    read_only=True,
    max_attempts=2,
    capability_version="phase-4d-v1-compat",
)

_STATUS_MAP: dict[ToolStatus, AgentResultStatus] = {
    ToolStatus.SUCCESS: AgentResultStatus.SUCCESS,
    ToolStatus.PARTIAL: AgentResultStatus.PARTIAL,
    ToolStatus.NEED_MORE_INFO: AgentResultStatus.NEED_MORE_INFO,
    ToolStatus.NO_MATCH: AgentResultStatus.NO_MATCH,
}


def _failure(
    *,
    intent: Intent,
    category: FailureCategory,
    suffix: str,
    message: str,
    retryable: bool = False,
) -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(
            category=category,
            code=f"legacy_{intent.value}_{suffix}",
            message=message,
            retryable=retryable,
        )
    )


def _policy_data(result: ToolResult) -> PolicyData | None:
    if not result.evidence:
        return None
    evidence: list[PolicyEvidenceData] = []
    for item in result.evidence:
        if not isinstance(item, PolicyEvidence):
            raise TypeError("policy result contains non-policy evidence")
        evidence.append(
            PolicyEvidenceData(
                evidence_id=item.evidence_id,
                title=item.title,
                source_url=item.source_url,
                excerpt=item.excerpt,
                document_no=item.document_no,
                published_at=item.published_at,
                source_org=item.source_org,
                section_path=item.section_path,
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                score=item.score,
                rerank_score=item.rerank_score,
            )
        )
    return PolicyData(evidence=evidence)


def _device_price_data(result: ToolResult) -> DevicePriceData | None:
    if not result.evidence:
        return None
    evidence: list[DevicePriceEvidenceData] = []
    for item in result.evidence:
        if not isinstance(item, DevicePriceEvidence):
            raise TypeError("device result contains non-device evidence")
        evidence.append(
            DevicePriceEvidenceData(
                evidence_id=item.evidence_id,
                title=item.title,
                brand=item.brand,
                model=item.model,
                specification=item.specification,
                price=item.price,
                currency=item.currency,
                source=item.source,
                observed_at=item.observed_at,
                availability=item.availability,
                source_url=item.source_url,
                original_price=item.original_price,
                original_price_type=item.original_price_type,
                official_product_id=item.official_product_id,
                official_sku_id=item.official_sku_id,
                match_score=item.match_score,
            )
        )
    return DevicePriceData(evidence=evidence)


def _policy_provenance(result: ToolResult) -> list[SourceReference]:
    return [
        SourceReference(
            source_type="policy_document",
            source_name=item.source_org or POLICY_TOOL_NAME,
            record_id=item.evidence_id,
            source_url=item.source_url,
        )
        for item in result.evidence
        if isinstance(item, PolicyEvidence)
    ]


def _device_price_provenance(result: ToolResult) -> list[SourceReference]:
    return [
        SourceReference(
            source_type="device_price_record",
            source_name=item.source or DEVICE_PRICE_TOOL_NAME,
            record_id=item.evidence_id,
            source_url=item.source_url,
        )
        for item in result.evidence
        if isinstance(item, DevicePriceEvidence)
    ]


class _LegacyAssistantToolAdapter:
    def __init__(
        self,
        tool: AssistantTool,
        *,
        mode: QueryMode,
        descriptor: ToolDescriptor,
        data_projector: Callable[
            [ToolResult], PolicyData | DevicePriceData | None
        ],
        provenance_projector: Callable[
            [ToolResult], list[SourceReference]
        ],
    ) -> None:
        if tool.name != descriptor.tool_name:
            raise ValueError(
                f"{mode.value} compatibility adapter requires "
                f"{descriptor.tool_name}, got {tool.name}"
            )
        self._tool = tool
        self._mode = mode
        self._descriptor = descriptor
        self._data_projector = data_projector
        self._provenance_projector = provenance_projector

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    async def _execute(self, question: str) -> AgentResult:
        try:
            result = await self._tool.execute(question)
            validate_tool_result(
                mode=self._mode,
                expected_tool_name=self._descriptor.tool_name,
                result=result,
            )
            if result.status is ToolStatus.ERROR:
                raise _failure(
                    intent=self._descriptor.intent,
                    category=FailureCategory.INTERNAL_ERROR,
                    suffix="tool_reported_error",
                    message="兼容工具返回了非结构化错误状态",
                )
            data = self._data_projector(result)
            provenance = self._provenance_projector(result)
            return AgentResult(
                tool=self._descriptor.tool_name,
                intent=self._descriptor.intent,
                status=_STATUS_MAP[result.status],
                answer=result.answer,
                data=data,
                warnings=list(result.warnings),
                missing_slots=list(result.missing_fields),
                reason_code=result.reason_code,
                provenance=provenance,
            )
        except AgentOperationError:
            raise
        except ToolUnavailableError as error:
            raise _failure(
                intent=self._descriptor.intent,
                category=FailureCategory.UPSTREAM_UNAVAILABLE,
                suffix="tool_unavailable",
                message="兼容工具依赖尚未就绪",
                retryable=True,
            ) from error
        except ToolContractError as error:
            raise _failure(
                intent=self._descriptor.intent,
                category=FailureCategory.CONTRACT_VIOLATION,
                suffix="contract_violation",
                message="兼容工具结果未通过 V1 契约校验",
            ) from error
        except TimeoutError as error:
            raise _failure(
                intent=self._descriptor.intent,
                category=FailureCategory.UPSTREAM_TIMEOUT,
                suffix="upstream_timeout",
                message="兼容工具依赖查询超时",
                retryable=True,
            ) from error
        except (PolicySourceError, PriceRepositoryError) as error:
            category = (
                FailureCategory.UPSTREAM_TIMEOUT
                if _caused_by_timeout(error)
                else FailureCategory.UPSTREAM_UNAVAILABLE
            )
            suffix = (
                "upstream_timeout"
                if category is FailureCategory.UPSTREAM_TIMEOUT
                else "upstream_failed"
            )
            raise _failure(
                intent=self._descriptor.intent,
                category=category,
                suffix=suffix,
                message="兼容工具依赖执行失败",
                retryable=True,
            ) from error
        except (AttributeError, TypeError, ValueError) as error:
            raise _failure(
                intent=self._descriptor.intent,
                category=FailureCategory.CONTRACT_VIOLATION,
                suffix="projection_invalid",
                message="兼容工具结果无法投影为 Agent 契约",
            ) from error
        except Exception as error:
            raise _failure(
                intent=self._descriptor.intent,
                category=FailureCategory.INTERNAL_ERROR,
                suffix="internal_error",
                message="兼容工具执行出现未分类错误",
            ) from error


class PolicyAssistantToolAdapter(_LegacyAssistantToolAdapter):
    """Expose the existing V1 policy tool through the typed Agent port.

    Lifecycle remains owned by the V1 composition root. This adapter only
    translates commands, results, and expected failures.
    """

    def __init__(self, tool: AssistantTool) -> None:
        super().__init__(
            tool,
            mode=QueryMode.POLICY,
            descriptor=POLICY_COMPAT_DESCRIPTOR,
            data_projector=_policy_data,
            provenance_projector=_policy_provenance,
        )

    async def execute(self, command: CommandModel) -> AgentResult:
        if not isinstance(command, PolicyCommand):
            raise _failure(
                intent=Intent.POLICY,
                category=FailureCategory.CONTRACT_VIOLATION,
                suffix="command_type_mismatch",
                message="政策兼容工具只接受 PolicyCommand",
            )
        return await self._execute(command.question)


class DevicePriceAssistantToolAdapter(_LegacyAssistantToolAdapter):
    """Expose the existing V1 device-price tool through the Agent port."""

    def __init__(self, tool: AssistantTool) -> None:
        super().__init__(
            tool,
            mode=QueryMode.DEVICE_PRICE,
            descriptor=DEVICE_PRICE_COMPAT_DESCRIPTOR,
            data_projector=_device_price_data,
            provenance_projector=_device_price_provenance,
        )

    async def execute(self, command: CommandModel) -> AgentResult:
        if not isinstance(command, DevicePriceCommand):
            raise _failure(
                intent=Intent.DEVICE_PRICE,
                category=FailureCategory.CONTRACT_VIOLATION,
                suffix="command_type_mismatch",
                message="设备价格兼容工具只接受 DevicePriceCommand",
            )
        return await self._execute(command.question)


def _caused_by_timeout(error: BaseException) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, TimeoutError):
            return True
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return False
