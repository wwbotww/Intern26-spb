from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .schemas import EvalCase


class DatasetError(ValueError):
    """Raised when an evaluation dataset is malformed."""


def load_dataset(path: Path) -> list[EvalCase]:
    if not path.is_file():
        raise DatasetError(f"数据集不存在：{path}")

    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            case = EvalCase.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DatasetError(
                f"{path}:{line_number} 样本格式错误：{exc}"
            ) from exc
        if case.id in seen_ids:
            raise DatasetError(
                f"{path}:{line_number} 样本 ID 重复：{case.id}"
            )
        seen_ids.add(case.id)
        cases.append(case)

    if not cases:
        raise DatasetError(f"数据集为空：{path}")
    return cases
