from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import TrackingCommand
from ..domain.failures import AgentFailure
from ..domain.results import TrackingData


class FakeTrackingGateway:
    """Deterministic, network-free gateway for the phase-1 vertical slice."""

    def __init__(
        self,
        records: Mapping[str, TrackingData] | None = None,
        *,
        scripted_failures: Iterable[AgentFailure] = (),
    ) -> None:
        self._records = dict(records or {})
        self._failures = list(scripted_failures)
        self.commands: list[TrackingCommand] = []

    async def query(self, command: TrackingCommand) -> TrackingData | None:
        self.commands.append(command)
        if self._failures:
            raise AgentOperationError(self._failures.pop(0))
        return self._records.get(command.mail_no)
