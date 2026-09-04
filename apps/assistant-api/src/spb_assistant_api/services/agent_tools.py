from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from ..domain.agent_actions import InvokeToolAction
from ..domain.agent_errors import AgentOperationError
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.ports import AgentTool, ToolExecutionRepository
from ..domain.results import AgentResult, AgentResultStatus
from ..domain.tooling import (
    CommandModel,
    ToolDescriptor,
    ToolExecutionReceipt,
)


def _contract_failure(code: str, message: str) -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(
            category=FailureCategory.CONTRACT_VIOLATION,
            code=code,
            message=message,
        )
    )


class AgentToolRegistry:
    """Deterministic Intent-to-tool whitelist for the Agent path."""

    def __init__(
        self,
        tools: Iterable[AgentTool],
        *,
        required_intents: frozenset[Intent] = frozenset(),
    ) -> None:
        by_intent: dict[Intent, AgentTool] = {}
        names: set[str] = set()
        for tool in tools:
            descriptor = tool.descriptor
            if descriptor.intent in by_intent:
                raise ValueError(
                    f"意图 {descriptor.intent.value} 只能注册一个默认工具"
                )
            if descriptor.tool_name in names:
                raise ValueError(
                    f"工具名重复: {descriptor.tool_name}"
                )
            if not descriptor.read_only:
                raise ValueError(
                    f"Agent 只允许只读工具: {descriptor.tool_name}"
                )
            by_intent[descriptor.intent] = tool
            names.add(descriptor.tool_name)

        missing = required_intents - set(by_intent)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"缺少 Agent 意图对应工具: {names}")
        self._tools = MappingProxyType(by_intent)

    @property
    def descriptors(self) -> Mapping[Intent, ToolDescriptor]:
        return MappingProxyType(
            {
                intent: tool.descriptor
                for intent, tool in self._tools.items()
            }
        )

    def get(self, intent: Intent) -> AgentTool:
        try:
            return self._tools[intent]
        except KeyError as error:
            raise _contract_failure(
                "unregistered_intent",
                f"意图 {intent.value} 没有已注册工具",
            ) from error


class AgentCommandDispatcher:
    def __init__(self, registry: AgentToolRegistry) -> None:
        self._registry = registry

    async def dispatch(
        self,
        *,
        tool_name: str,
        command: CommandModel,
    ) -> AgentResult:
        intent = Intent(command.intent)
        tool = self._registry.get(intent)
        descriptor = tool.descriptor
        if tool_name != descriptor.tool_name:
            raise _contract_failure(
                "tool_route_mismatch",
                "待执行工具与服务端白名单路由不一致",
            )
        if not isinstance(command, descriptor.command_type):
            raise _contract_failure(
                "command_type_mismatch",
                f"工具 {tool_name} 收到错误的命令类型",
            )

        result = await tool.execute(command)
        if result.tool != descriptor.tool_name:
            raise _contract_failure(
                "result_tool_mismatch",
                f"工具 {tool_name} 返回了不匹配的工具标识",
            )
        if result.intent != descriptor.intent:
            raise _contract_failure(
                "result_intent_mismatch",
                f"工具 {tool_name} 返回了不匹配的意图",
            )
        return result


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    result: AgentResult
    reused: bool


class ToolExecutor:
    def __init__(
        self,
        dispatcher: AgentCommandDispatcher,
        receipts: ToolExecutionRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._receipts = receipts
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(
        self,
        *,
        conversation_id: str,
        action: InvokeToolAction,
    ) -> ToolExecutionOutcome:
        existing = await self._receipts.find(
            conversation_id=conversation_id,
            argument_fingerprint=action.argument_fingerprint,
        )
        if existing is not None:
            if (
                existing.tool_name != action.tool_name
                or existing.tool_call_id != action.tool_call_id
            ):
                raise _contract_failure(
                    "receipt_identity_mismatch",
                    "执行收据与当前工具调用身份不一致",
                )
            return ToolExecutionOutcome(result=existing.result, reused=True)

        now = self._clock()
        if now.tzinfo is None or action.deadline_at.tzinfo is None:
            raise _contract_failure(
                "deadline_without_timezone",
                "工具执行 deadline 必须包含时区",
            )
        if now >= action.deadline_at:
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.LOOP_BUDGET_EXCEEDED,
                    code="request_deadline_exceeded",
                    message="工具执行前已超过本轮 deadline",
                )
            )
        result = await self._dispatcher.dispatch(
            tool_name=action.tool_name,
            command=action.command,
        )
        if result.status is not AgentResultStatus.FAILED:
            await self._receipts.save(
                ToolExecutionReceipt(
                    conversation_id=conversation_id,
                    tool_call_id=action.tool_call_id,
                    tool_name=action.tool_name,
                    argument_fingerprint=action.argument_fingerprint,
                    result=result,
                    completed_at=self._clock(),
                )
            )
        return ToolExecutionOutcome(result=result, reused=False)
