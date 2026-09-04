from __future__ import annotations

from collections.abc import Mapping

from ..domain.exceptions import ToolContractError
from ..domain.models import EvidenceType, QueryMode, ToolResult, ToolStatus
from ..domain.ports import AssistantTool


POLICY_TOOL_NAME = "policy_knowledge"
DEVICE_PRICE_TOOL_NAME = "device_price"
EXPECTED_TOOL_NAMES: dict[QueryMode, str] = {
    QueryMode.POLICY: POLICY_TOOL_NAME,
    QueryMode.DEVICE_PRICE: DEVICE_PRICE_TOOL_NAME,
}


class ToolRegistry:
    def __init__(
        self,
        tools: Mapping[QueryMode, AssistantTool],
    ) -> None:
        missing = set(QueryMode) - set(tools)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"缺少查询模式对应工具: {names}")

        resolved: dict[QueryMode, AssistantTool] = {}
        for mode, expected_name in EXPECTED_TOOL_NAMES.items():
            tool = tools[mode]
            if tool.name != expected_name:
                raise ValueError(
                    f"{mode.value} 必须映射到 {expected_name}，"
                    f"实际为 {tool.name}"
                )
            resolved[mode] = tool
        self._tools = resolved

    def get(self, mode: QueryMode) -> AssistantTool:
        return self._tools[mode]

    def readiness(self) -> dict[str, str]:
        return {
            tool.name: tool.readiness()
            for tool in self._tools.values()
        }

    async def initialize(self) -> None:
        initialized: set[int] = set()
        for tool in self._tools.values():
            identity = id(tool)
            if identity in initialized:
                continue
            initialized.add(identity)
            await tool.initialize()

    async def close(self) -> None:
        closed: set[int] = set()
        for tool in self._tools.values():
            identity = id(tool)
            if identity in closed:
                continue
            closed.add(identity)
            await tool.close()


class QueryDispatcher:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def dispatch(
        self,
        *,
        mode: QueryMode,
        question: str,
    ) -> ToolResult:
        tool = self._registry.get(mode)
        result = await tool.execute(question)
        validate_tool_result(
            mode=mode,
            expected_tool_name=tool.name,
            result=result,
        )
        return result


def validate_tool_result(
    *,
    mode: QueryMode,
    expected_tool_name: str,
    result: ToolResult,
) -> None:
    """Validate the shared V1 result contract before any API projection."""

    if result.tool != expected_tool_name:
        raise ToolContractError(
            f"工具 {expected_tool_name} 返回了不匹配的标识 {result.tool}"
        )
    if (
        result.status in {ToolStatus.SUCCESS, ToolStatus.PARTIAL}
        and not result.evidence
    ):
        raise ToolContractError("成功或部分成功结果必须包含证据")
    if (
        result.status in {ToolStatus.SUCCESS, ToolStatus.PARTIAL}
        and not result.answer.strip()
    ):
        raise ToolContractError("成功或部分成功结果必须包含回答")
    if (
        result.status in {ToolStatus.NO_MATCH, ToolStatus.NEED_MORE_INFO}
        and result.evidence
    ):
        raise ToolContractError(
            "无匹配或信息不足结果不应包含事实证据"
        )
    expected_type = (
        EvidenceType.POLICY
        if mode is QueryMode.POLICY
        else EvidenceType.DEVICE_PRICE
    )
    if any(item.type is not expected_type for item in result.evidence):
        raise ToolContractError(
            f"{mode.value} 工具返回了错误类型的证据"
        )
