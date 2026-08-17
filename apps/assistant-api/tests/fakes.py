from __future__ import annotations

from spb_assistant_api.domain.models import ToolResult


class FakeTool:
    def __init__(
        self,
        *,
        name: str,
        result: ToolResult,
        ready: str = "ready",
    ) -> None:
        self._name = name
        self.result = result
        self.ready = ready
        self.questions: list[str] = []
        self.initialized = False
        self.closed = False

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self) -> None:
        self.initialized = True

    async def execute(self, question: str) -> ToolResult:
        self.questions.append(question)
        return self.result

    def readiness(self) -> str:
        return self.ready

    async def close(self) -> None:
        self.closed = True
