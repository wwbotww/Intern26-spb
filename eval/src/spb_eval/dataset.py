from __future__ import annotations

import json
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .schemas import AssistantEvalCase, EvalCase


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


def load_assistant_dataset(path: Path) -> list[AssistantEvalCase]:
    if not path.is_file():
        raise DatasetError(f"数据集不存在：{path}")

    cases: list[AssistantEvalCase] = []
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
            case = AssistantEvalCase.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DatasetError(
                f"{path}:{line_number} assistant 样本格式错误：{exc}"
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


def _write_jsonl(path: Path, cases: list[EvalCase]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for case in cases:
            handle.write(
                json.dumps(
                    case.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_dataset(
    source: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Export calibration/holdout files and a reproducibility manifest."""
    cases = load_dataset(source)
    grouped = {
        split: [case for case in cases if case.split == split]
        for split in ("calibration", "holdout")
    }
    if not grouped["calibration"] or not grouped["holdout"]:
        raise DatasetError(
            "数据集必须同时包含 calibration 和 holdout 样本"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        split: output_dir / f"{split}.jsonl"
        for split in grouped
    }
    manifest_path = output_dir / "manifest.json"
    existing = [
        path
        for path in [*targets.values(), manifest_path]
        if path.exists()
    ]
    if existing:
        raise DatasetError(
            "目标文件已存在，拒绝覆盖："
            + "、".join(str(path) for path in existing)
        )
    for split, path in targets.items():
        _write_jsonl(path, grouped[split])
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source": str(source),
        "source_sha256": _sha256(source),
        "total_cases": len(cases),
        "outcomes": dict(
            sorted(
                Counter(
                    case.expected_outcome for case in cases
                ).items()
            )
        ),
        "categories": dict(
            sorted(Counter(case.category for case in cases).items())
        ),
        "unique_gold_documents": len(
            {
                document_id
                for case in cases
                for document_id in case.gold_document_ids
            }
        ),
        "splits": {
            split: {
                "path": str(path),
                "sha256": _sha256(path),
                "cases": len(grouped[split]),
                "outcomes": dict(
                    sorted(
                        Counter(
                            case.expected_outcome
                            for case in grouped[split]
                        ).items()
                    )
                ),
                "categories": dict(
                    sorted(
                        Counter(
                            case.category for case in grouped[split]
                        ).items()
                    )
                ),
            }
            for split, path in targets.items()
        },
    }
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "manifest": str(manifest_path),
        "calibration": str(targets["calibration"]),
        "holdout": str(targets["holdout"]),
    }
