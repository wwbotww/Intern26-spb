from __future__ import annotations

from typing import Protocol


class VectorSink(Protocol):
    """向量目标接口。当前前置流水线使用 JsonlSink。"""

    def ensure_collection(self, dimension: int) -> None: ...

    def upsert(self, records: list[dict]) -> None: ...

