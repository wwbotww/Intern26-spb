from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: int


class SlidingWindowRateLimiter:
    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> RateLimitResult:
        now = monotonic()
        cutoff = now - self._window_seconds
        async with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                retry_after = max(
                    1,
                    math.ceil(
                        self._window_seconds - (now - bucket[0])
                    ),
                )
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    retry_after=retry_after,
                )
            bucket.append(now)
            return RateLimitResult(
                allowed=True,
                remaining=max(0, self._limit - len(bucket)),
                retry_after=0,
            )
