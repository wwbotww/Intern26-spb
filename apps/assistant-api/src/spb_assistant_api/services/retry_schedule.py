from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..domain.failures import AgentFailure


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    """Bounded exponential backoff with reproducible per-request jitter."""

    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 2.0
    jitter_ratio: float = 0.20

    def __post_init__(self) -> None:
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds 不能小于 0")
        if self.max_delay_seconds <= 0:
            raise ValueError("max_delay_seconds 必须大于 0")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("base_delay_seconds 不能超过最大延迟")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio 必须介于 0 和 1")

    def delay_seconds(
        self,
        *,
        failure: AgentFailure,
        retry_number: int,
        jitter_key: str,
    ) -> float | None:
        if retry_number < 1:
            raise ValueError("retry_number 必须大于 0")
        if not jitter_key:
            raise ValueError("jitter_key 不能为空")
        if failure.retry_after_seconds is not None:
            if failure.retry_after_seconds > self.max_delay_seconds:
                return None
            return failure.retry_after_seconds

        exponential = min(
            self.base_delay_seconds
            * (2.0 ** min(retry_number - 1, 30)),
            self.max_delay_seconds,
        )
        if exponential == 0 or self.jitter_ratio == 0:
            return exponential
        digest = hashlib.sha256(
            f"{jitter_key}:{retry_number}".encode("utf-8")
        ).digest()
        unit = int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)
        factor = 1 - self.jitter_ratio + (2 * self.jitter_ratio * unit)
        return min(exponential * factor, self.max_delay_seconds)
