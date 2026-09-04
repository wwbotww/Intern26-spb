from __future__ import annotations

import json
from pathlib import Path

import pytest

from spb_eval.dataset import (
    DatasetError,
    load_agent_understanding_dataset,
    load_dataset,
    split_dataset,
)


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
                    '"gold_document_ids":["doc-1","doc-1"],'
                    '"difficulty":"hard","split":"holdout",'
                    '"source_type":"ocr",'
                    '"tags":["numeric","numeric"]}'
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
    assert cases[0].difficulty == "hard"
    assert cases[0].split == "holdout"
    assert cases[0].source_type == "ocr"
    assert cases[0].tags == ["numeric"]


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


def test_split_dataset_writes_manifest_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "full.jsonl"
    dataset.write_text(
        "\n".join(
            [
                (
                    '{"id":"a","category":"direct","question":"问题",'
                    '"expected_outcome":"answer",'
                    '"gold_document_ids":["doc-1"],'
                    '"split":"calibration"}'
                ),
                (
                    '{"id":"b","category":"ood","question":"无关问题",'
                    '"expected_outcome":"reject","split":"holdout"}'
                ),
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "split"

    result = split_dataset(dataset, output)

    assert Path(result["calibration"]).is_file()
    assert Path(result["holdout"]).is_file()
    manifest = json.loads(
        Path(result["manifest"]).read_text(encoding="utf-8")
    )
    assert manifest["total_cases"] == 2
    assert manifest["unique_gold_documents"] == 1
    assert manifest["splits"]["calibration"]["cases"] == 1
    with pytest.raises(DatasetError, match="拒绝覆盖"):
        split_dataset(dataset, output)


def test_public_agent_understanding_dataset_has_phase2_coverage() -> None:
    workspace_root = Path(__file__).resolve().parents[2]
    cases = load_agent_understanding_dataset(
        workspace_root
        / "eval"
        / "datasets"
        / "agent-understanding-v1.jsonl"
    )

    intents = {
        turn.expected_intent for case in cases for turn in case.turns
    }
    categories = {case.category for case in cases}
    splits = {case.split for case in cases}

    assert len(cases) == 18
    assert intents == {
        "policy",
        "device_price",
        "tracking",
        "delivery_time",
        "postage",
        "unknown",
    }
    assert {"multi_intent", "multi_turn_slot_fill", "intent_switch"} <= (
        categories
    )
    assert splits == {"calibration", "holdout"}
