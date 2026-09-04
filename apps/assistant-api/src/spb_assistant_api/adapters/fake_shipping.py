from __future__ import annotations

from collections.abc import Iterable

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import DeliveryTimeCommand, PostageCommand
from ..domain.failures import AgentFailure
from ..domain.results import DeliveryTimeData, PostageData


class FakeDeliveryTimeGateway:
    """Deterministic phase-3A fake; it does not model an upstream wire API."""

    def __init__(
        self,
        result: DeliveryTimeData | None = None,
        *,
        scripted_failures: Iterable[AgentFailure] = (),
    ) -> None:
        self._result = result
        self._failures = list(scripted_failures)
        self.commands: list[DeliveryTimeCommand] = []

    async def query(
        self,
        command: DeliveryTimeCommand,
    ) -> DeliveryTimeData | None:
        self.commands.append(command)
        if self._failures:
            raise AgentOperationError(self._failures.pop(0))
        return self._result


class FakePostageGateway:
    """Deterministic phase-3A fake; prices must be supplied by the fixture."""

    def __init__(
        self,
        result: PostageData | None = None,
        *,
        scripted_failures: Iterable[AgentFailure] = (),
    ) -> None:
        self._result = result
        self._failures = list(scripted_failures)
        self.commands: list[PostageCommand] = []

    async def quote(self, command: PostageCommand) -> PostageData | None:
        self.commands.append(command)
        if self._failures:
            raise AgentOperationError(self._failures.pop(0))
        return self._result
