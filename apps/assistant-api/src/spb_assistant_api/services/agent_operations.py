from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Protocol

from ..observability.metrics import ServiceMetrics


logger = logging.getLogger(__name__)


class _CleanupResult(Protocol):
    expired_conversations: int
    deleted_idempotency_receipts: int
    deleted_tool_receipts: int
    failures: tuple[str, ...]


class _ConversationJanitor(Protocol):
    async def cleanup_expired(self) -> _CleanupResult: ...


class AgentJanitorScheduler:
    """Lifecycle-owned periodic cleanup with observable degraded state."""

    def __init__(
        self,
        *,
        janitor: _ConversationJanitor,
        metrics: ServiceMetrics,
        interval_seconds: float,
        timeout_seconds: float,
    ) -> None:
        if interval_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("janitor interval 和 timeout 必须大于 0")
        self._janitor = janitor
        self._metrics = metrics
        self._interval_seconds = interval_seconds
        self._timeout_seconds = timeout_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._status = "starting"

    @property
    def readiness(self) -> str:
        return self._status

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Agent janitor 已启动")
        await self.run_once()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="agent-conversation-janitor",
        )

    async def close(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        try:
            await task
        finally:
            self._task = None

    async def run_once(self) -> None:
        started = perf_counter()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._janitor.cleanup_expired()
        except TimeoutError:
            self._status = "degraded"
            self._metrics.observe_agent_janitor(
                outcome="timeout",
                duration_seconds=perf_counter() - started,
            )
            logger.warning(
                "agent_janitor_timeout",
                extra={"timeout_seconds": self._timeout_seconds},
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._status = "degraded"
            self._metrics.observe_agent_janitor(
                outcome="error",
                duration_seconds=perf_counter() - started,
            )
            logger.error(
                "agent_janitor_failed",
                extra={"exception_type": type(error).__name__},
            )
        else:
            partial = bool(result.failures)
            self._status = "degraded" if partial else "ready"
            self._metrics.observe_agent_janitor(
                outcome="partial" if partial else "success",
                duration_seconds=perf_counter() - started,
                expired_conversations=result.expired_conversations,
                deleted_idempotency_receipts=(
                    result.deleted_idempotency_receipts
                ),
                deleted_tool_receipts=result.deleted_tool_receipts,
            )
            logger.info(
                "agent_janitor_completed",
                extra={
                    "outcome": "partial" if partial else "success",
                    "expired_conversations": result.expired_conversations,
                    "deleted_idempotency_receipts": (
                        result.deleted_idempotency_receipts
                    ),
                    "deleted_tool_receipts": result.deleted_tool_receipts,
                    "failure_count": len(result.failures),
                },
            )

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._interval_seconds,
                )
            except TimeoutError:
                await self.run_once()
