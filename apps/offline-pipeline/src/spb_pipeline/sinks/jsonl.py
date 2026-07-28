from __future__ import annotations

from pathlib import Path

from ..io_utils import write_jsonl_atomic


class JsonlSink:
    def __init__(self, path: Path):
        self.path = path

    def ensure_collection(self, dimension: int) -> None:
        if dimension < 0:
            raise ValueError("dimension 不能为负数")

    def upsert(self, records: list[dict]) -> None:
        write_jsonl_atomic(self.path, records)
