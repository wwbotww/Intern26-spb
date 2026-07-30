from __future__ import annotations

from pathlib import Path

import pytest

from spb_eval.dataset import DatasetError, load_dataset


def test_load_dataset_validates_and_deduplicates_labels(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        "\n".join(
            [
                (
                    '{"id":"a","category":"direct","question":"问题",'
                    '"expected_outcome":"answer",'
                    '"gold_document_ids":["doc-1","doc-1"]}'
                ),
                (
                    '{"id":"b","category":"ood","question":"无关问题",'
                    '"expected_outcome":"reject"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    cases = load_dataset(dataset)

    assert [case.id for case in cases] == ["a", "b"]
    assert cases[0].gold_document_ids == ["doc-1"]


def test_load_dataset_rejects_answer_without_gold(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        (
            '{"id":"a","category":"direct","question":"问题",'
            '"expected_outcome":"answer"}'
        ),
        encoding="utf-8",
    )

    with pytest.raises(DatasetError, match="gold_document_ids"):
        load_dataset(dataset)


def test_load_dataset_rejects_duplicate_ids(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    line = (
        '{"id":"same","category":"ood","question":"无关问题",'
        '"expected_outcome":"reject"}'
    )
    dataset.write_text(f"{line}\n{line}\n", encoding="utf-8")

    with pytest.raises(DatasetError, match="样本 ID 重复"):
        load_dataset(dataset)
