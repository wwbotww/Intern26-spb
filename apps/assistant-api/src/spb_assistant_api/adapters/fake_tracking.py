from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import TrackingCommand
from ..domain.failures import AgentFailure
from ..domain.results import TrackingData
from ..domain.tracking import TrackingQueryResult, TrackingSource


class FakeTrackingGateway:
    """Deterministic, network-free gateway for the phase-1 vertical slice."""

    def __init__(
        self,
        records: Mapping[str, TrackingData] | None = None,
        *,
        scripted_failures: Iterable[AgentFailure] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._records = dict(records or {})
        self._failures = list(scripted_failures)
        self._clock = clock or (lambda: datetime.now(UTC))
        self.commands: list[TrackingCommand] = []

    async def query(self, command: TrackingCommand) -> TrackingQueryResult:
        self.commands.append(command)
        if self._failures:
            raise AgentOperationError(self._failures.pop(0))
        data = self._records.get(command.mail_no)
        now = self._clock()
        return TrackingQueryResult(
            data=(
                data.model_copy(deep=True, update={"queried_at": now})
                if data is not None
                else None
            ),
            source=TrackingSource(
                source_type="fake_gateway",
                source_name="phase-1-tracking-fixture",
                profile="synthetic-v1",
            ),
            queried_at=now,
            history_completeness="unknown",
        )
