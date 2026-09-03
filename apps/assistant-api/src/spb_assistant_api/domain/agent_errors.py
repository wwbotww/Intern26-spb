from __future__ import annotations

from .failures import AgentFailure


class AgentOperationError(RuntimeError):
    """Expected Agent failure crossing a service or workflow boundary."""

    def __init__(self, failure: AgentFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure
