from __future__ import annotations

from ..domain.agent_errors import AgentOperationError
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.tooling import ToolExecutionReceipt


class InMemoryToolExecutionRepository:
    """Phase-1 receipt store used to prove replay-safe tool execution."""

    def __init__(self) -> None:
        self._receipts: dict[tuple[str, str], ToolExecutionReceipt] = {}

    async def find(
        self,
        *,
        conversation_id: str,
        argument_fingerprint: str,
    ) -> ToolExecutionReceipt | None:
        return self._receipts.get(
            (conversation_id, argument_fingerprint)
        )

    async def save(self, receipt: ToolExecutionReceipt) -> None:
        key = (receipt.conversation_id, receipt.argument_fingerprint)
        existing = self._receipts.get(key)
        if existing is not None and existing != receipt:
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.STATE_CONFLICT,
                    code="tool_receipt_conflict",
                    message="相同参数指纹已存在不同的执行收据",
                )
            )
        self._receipts[key] = receipt

    async def delete_conversation(self, conversation_id: str) -> int:
        keys = [
            key for key in self._receipts if key[0] == conversation_id
        ]
        for key in keys:
            del self._receipts[key]
        return len(keys)

    def __len__(self) -> int:
        return len(self._receipts)
