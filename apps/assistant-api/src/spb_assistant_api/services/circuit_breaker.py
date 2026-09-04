from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from ..domain.agent_errors import AgentOperationError
from ..domain.failures import AgentFailure, FailureCategory


_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    capability: str
    state: CircuitState
    consecutive_failures: int
    retry_after_seconds: float | None = None


@dataclass(slots=True)
class _CircuitRecord:
    consecutive_failures: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False


class CapabilityCircuitBreaker:
    """Small in-process breaker whose state is isolated per capability."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold 必须大于 0")
        if recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds 必须大于 0")
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._clock = clock or time.monotonic
        self._records: dict[str, _CircuitRecord] = {}
        self._lock = asyncio.Lock()

    async def before_call(self, capability: str) -> None:
        normalized = _validate_capability(capability)
        async with self._lock:
            record = self._records.setdefault(
                normalized,
                _CircuitRecord(),
            )
            if record.opened_at is None:
                return
            remaining = self._remaining(record, self._clock())
            if remaining > 0 or record.probe_in_flight:
                raise _circuit_open(normalized, max(remaining, 0.0))
            record.probe_in_flight = True

    async def record_success(self, capability: str) -> None:
        normalized = _validate_capability(capability)
        async with self._lock:
            self._records.pop(normalized, None)

    async def record_failure(self, capability: str) -> None:
        normalized = _validate_capability(capability)
        async with self._lock:
            record = self._records.setdefault(
                normalized,
                _CircuitRecord(),
            )
            record.consecutive_failures += 1
            if (
                record.probe_in_flight
                or record.consecutive_failures >= self._failure_threshold
            ):
                record.opened_at = self._clock()
                record.probe_in_flight = False

    async def record_aborted(self, capability: str) -> None:
        """Release a cancelled half-open probe without changing health."""

        normalized = _validate_capability(capability)
        async with self._lock:
            record = self._records.get(normalized)
            if record is not None:
                record.probe_in_flight = False
                if (
                    record.opened_at is None
                    and record.consecutive_failures == 0
                ):
                    self._records.pop(normalized, None)

    async def snapshot(self, capability: str) -> CircuitSnapshot:
        normalized = _validate_capability(capability)
        async with self._lock:
            record = self._records.get(normalized)
            if record is None:
                return CircuitSnapshot(
                    capability=normalized,
                    state=CircuitState.CLOSED,
                    consecutive_failures=0,
                )
            if record.opened_at is None:
                state = CircuitState.CLOSED
                retry_after = None
            else:
                remaining = self._remaining(record, self._clock())
                state = (
                    CircuitState.OPEN
                    if remaining > 0
                    else CircuitState.HALF_OPEN
                )
                retry_after = max(remaining, 0.0)
            return CircuitSnapshot(
                capability=normalized,
                state=state,
                consecutive_failures=record.consecutive_failures,
                retry_after_seconds=retry_after,
            )

    def _remaining(self, record: _CircuitRecord, now: float) -> float:
        assert record.opened_at is not None
        elapsed = max(now - record.opened_at, 0.0)
        return self._recovery_timeout_seconds - elapsed


def _validate_capability(value: str) -> str:
    normalized = value.strip().lower()
    if not _CAPABILITY_NAME.fullmatch(normalized):
        raise ValueError("capability 必须是稳定的小写标识")
    return normalized


def _circuit_open(
    capability: str,
    retry_after_seconds: float,
) -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(
            category=FailureCategory.UPSTREAM_UNAVAILABLE,
            code="capability_circuit_open",
            message=f"能力 {capability} 的上游熔断器已打开",
            retryable=True,
            retry_after_seconds=retry_after_seconds,
        )
    )
