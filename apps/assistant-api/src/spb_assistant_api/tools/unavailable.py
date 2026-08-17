from __future__ import annotations

from ..domain.exceptions import ToolUnavailableError
from ..domain.models import ToolResult


class UnavailableTool:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self) -> None:
        return None

    async def execute(self, question: str) -> ToolResult:
        del question
        raise ToolUnavailableError(self.name)

    def readiness(self) -> str:
        return "not_ready"

    async def close(self) -> None:
        return None
