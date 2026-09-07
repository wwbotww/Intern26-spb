from __future__ import annotations

import json
from pathlib import Path

import pytest
from spb_eval.cli import main
from spb_eval.understanding_dataset import (
    UnderstandingDataError,
    build_requests,
    digest,
    load_understanding_cases,
)
from spb_eval.understanding_review import (
    freeze_understanding_holdout,
    prepare_understanding_review,
)
from spb_eval.understanding_schema import UnderstandingCase


def _case(name: str, message: str | None = None, **overrides) -> dict:
    return UnderstandingCase.model_validate(
        {
            "id": name,
            "group_id": name,
            "category": "synthetic_test",
            "input": {"message": message or f"synthetic {name}"},
            "gold": {"intent": "tracking", "missing_slots": ["mail_no"]},
            **overrides,
        }
    ).model_dump(mode="json")


def _write(path: Path, cases: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(case) for case in cases) + "\n", encoding="utf-8"
    )
    return path


def _setup(tmp_path: Path, candidates: list[dict] | None = None):
    dataset = _write(
        tmp_path / "candidate.jsonl", candidates or [_case("new")]
    )
    known = _write(
        tmp_path / "known.jsonl", [_case("known", "previously_seen_question")]
    )
    output = tmp_path / "review"
    prepare_understanding_review(dataset, [known], output)
    return dataset, known, output / "review.json"


def _decide(path: Path, **overrides) -> dict:
    # Synthetic test attestation; never used to approve actual candidate data.
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(
        reviewer_id="fixture-reviewer",
        reviewed_at="2026-01-01T00:00:00+00:00",
        annotations_checked=True,
        semantic_overlap_checked=True,
    )
    for case in record["cases"]:
        case["decision"] = "approve"
    record.update(overrides)
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


def test_review_is_pending_has_no_query_or_gold_and_never_mutates_inputs(
    tmp_path,
):
    dataset, known, review = _setup(
        tmp_path, [_case("new", "PRIVATE_question_987")]
    )
    original = dataset.read_bytes()
    contents = review.read_text()
    record = json.loads(contents)
    assert record["reviewer_id"] is None and record["reviewed_at"] is None
    assert (
        not record["annotations_checked"]
        and not record["semantic_overlap_checked"]
    )
    assert all(case["decision"] == "pending" for case in record["cases"])
    for file in review.parent.iterdir():
        text = file.read_text()
        assert (
            "PRIVATE_question_987" not in text
            and "previously_seen_question" not in text
        )
        assert '"gold"' not in text and '"input"' not in text
    with pytest.raises(
        UnderstandingDataError, match="human_review_attestation_required"
    ):
        freeze_understanding_holdout(
            dataset, [known], review, tmp_path / "frozen"
        )
    assert not (tmp_path / "frozen").exists()
    assert dataset.read_bytes() == original


@pytest.mark.parametrize(
    "change,issue",
    [
        ({"id": "known"}, "known_case_id"),
        ({"group_id": "known"}, "known_semantic_group"),
        (
            {"input": {"message": "  PREVIOUSLY_SEEN_QUESTION \n"}},
            "known_normalized_input",
        ),
        ({"tags": ["seen_smoke"]}, "seen_smoke"),
    ],
)
def test_known_contamination_is_recomputed_and_cannot_be_approved(
    tmp_path, change, issue
):
    dataset, known, review = _setup(tmp_path, [_case("new", **change)])
    audit = json.loads((review.parent / "audit.json").read_text())
    assert issue in audit["cases"][0]["issues"]
    assert audit["blocked_cases"] == 1
    # Tampering with the displayed audit must not bypass the actual check.
    (review.parent / "audit.json").write_text("{}")
    _decide(review)
    with pytest.raises(
        UnderstandingDataError, match="approved_case_has_known_contamination"
    ):
        freeze_understanding_holdout(
            dataset, [known], review, tmp_path / "frozen"
        )
    assert not (tmp_path / "frozen").exists()


@pytest.mark.parametrize(
    "override",
    [
        {"reviewer_id": None},
        {"reviewed_at": None},
        {"reviewed_at": "2099-01-01T00:00:00+00:00"},
        {"annotations_checked": False},
        {"semantic_overlap_checked": False},
    ],
)
def test_freeze_requires_complete_explicit_review_attestation(
    tmp_path, override
):
    dataset, known, review = _setup(tmp_path)
    _decide(review, **override)
    with pytest.raises(
        UnderstandingDataError, match="human_review_attestation_required"
    ):
        freeze_understanding_holdout(
            dataset, [known], review, tmp_path / "frozen"
        )


@pytest.mark.parametrize(
    "change,code",
    [
        ("gold", "review_dataset_or_references_changed"),
        ("reference", "review_dataset_or_references_changed"),
        ("pending", "pending_review_cannot_freeze"),
        ("duplicate", "review_must_cover_each_case_once"),
        ("unknown_id", "review_must_cover_each_case_once"),
        ("case_hash", "review_case_fingerprint_mismatch"),
        ("all_excluded", "empty_approved_holdout"),
        ("excluded_without_reason", "invalid_review_contract"),
        ("naive_date", "invalid_review_contract"),
    ],
)
def test_changed_or_incomplete_review_is_rejected_before_writes(
    tmp_path, change, code
):
    dataset, known, review = _setup(tmp_path)
    record = _decide(review)
    if change == "gold":
        _write(dataset, [_case("new", gold={"intent": "unknown"})])
    elif change == "reference":
        _write(known, [_case("new-reference")])
    elif change == "pending":
        record["cases"][0]["decision"] = "pending"
    elif change == "duplicate":
        record["cases"].append(record["cases"][0].copy())
    elif change == "unknown_id":
        record["cases"][0]["id"] = "foreign"
    elif change == "case_hash":
        record["cases"][0]["case_sha256"] = "0" * 64
    elif change in {"all_excluded", "excluded_without_reason"}:
        record["cases"][0].update(
            decision="exclude",
            reason_code="not_in_scope" if change == "all_excluded" else None,
        )
    else:
        record["reviewed_at"] = "2026-01-01T00:00:00"
    review.write_text(json.dumps(record))
    with pytest.raises(UnderstandingDataError, match=code):
        freeze_understanding_holdout(
            dataset, [known], review, tmp_path / "frozen"
        )
    assert not (tmp_path / "frozen").exists()


def test_freeze_preserves_exclusions_hashes_and_emits_gold_free_requests(
    tmp_path,
):
    dataset, known, review = _setup(
        tmp_path,
        [
            _case("known", "previously_seen_question"),
            _case(
                "new",
                "查看邮件1234567890123",
                gold={
                    "intent": "tracking",
                    "slot_values": {"mail_no": "1234567890123"},
                },
            ),
        ],
    )
    original = dataset.read_bytes()
    record = _decide(review)
    record["cases"][0].update(decision="exclude", reason_code="known_input")
    review.write_text(json.dumps(record))
    output = tmp_path / "frozen"
    result = freeze_understanding_holdout(dataset, [known], review, output)
    dataset_hash, cases = load_understanding_cases(
        output / "holdout.jsonl", "holdout"
    )
    assert [case.id for case in cases] == ["new"]
    assert cases[0].annotation_status == "reviewed"
    assert dataset_hash == result["dataset_sha256"]
    assert dataset.read_bytes() == original
    requests = json.loads((output / "requests.json").read_text())
    assert requests == build_requests(dataset_hash, cases)
    assert '"gold"' not in json.dumps(requests)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["review_sha256"] == digest(
        json.loads((output / "review.json").read_text())
    )
    assert manifest["request_sha256"] == digest(requests)
    assert manifest["approved_cases"] == manifest["excluded_cases"] == 1
    assert manifest["intent_support"]["tracking"] == 1
    assert len(manifest["missing_intent_classes"]) == 5
    assert manifest["gold_hard_values"] == 1
    with pytest.raises(FileExistsError):
        freeze_understanding_holdout(dataset, [known], review, output)
    with pytest.raises(FileExistsError):
        prepare_understanding_review(dataset, [known], review.parent)


def test_reference_order_is_irrelevant_but_omitting_one_invalidates_review(
    tmp_path,
):
    dataset, known, review = _setup(tmp_path)
    other = _write(tmp_path / "other.jsonl", [_case("other")])
    directory = tmp_path / "both-review"
    prepare_understanding_review(dataset, [known, other], directory)
    review = directory / "review.json"
    _decide(review)
    with pytest.raises(
        UnderstandingDataError, match="review_dataset_or_references_changed"
    ):
        freeze_understanding_holdout(
            dataset, [known], review, tmp_path / "missing"
        )
    assert (
        freeze_understanding_holdout(
            dataset, [other, known], review, tmp_path / "frozen"
        )["cases"]
        == 1
    )
    with pytest.raises(
        UnderstandingDataError, match="duplicate_reference_dataset"
    ):
        prepare_understanding_review(
            dataset, [known, known], tmp_path / "duplicate"
        )


def test_cli_handoff_does_not_imply_approval_or_model_authorization(
    tmp_path, capsys
):
    dataset, known, review = _setup(tmp_path)
    args = ["--dataset", str(dataset), "--against", str(known)]
    assert (
        main(
            [
                "understanding-review",
                *args,
                "--output-dir",
                str(tmp_path / "cli-review"),
            ]
        )
        == 0
    )
    assert "pending_review" in capsys.readouterr().out
    assert (
        main(
            [
                "understanding-freeze",
                *args,
                "--review",
                str(review),
                "--output-dir",
                str(tmp_path / "frozen"),
            ]
        )
        == 2
    )
    assert "human_review_attestation_required" in capsys.readouterr().err
    assert not (tmp_path / "frozen").exists()


def test_review_rejects_duplicate_keys_and_non_boolean_attestation(tmp_path):
    dataset, known, review = _setup(tmp_path)
    record = _decide(review, annotations_checked="true")
    with pytest.raises(
        UnderstandingDataError, match="invalid_review_contract"
    ):
        freeze_understanding_holdout(
            dataset, [known], review, tmp_path / "invalid-bool"
        )
    record["annotations_checked"] = True
    raw = json.dumps(record)
    review.write_text('{"reviewer_id":"forged",' + raw[1:])
    with pytest.raises(
        UnderstandingDataError, match="invalid_review_contract"
    ):
        freeze_understanding_holdout(
            dataset, [known], review, tmp_path / "duplicate-key"
        )


def test_review_refuses_prelabelled_holdout_or_no_reference(tmp_path):
    known = _write(tmp_path / "known.jsonl", [_case("known")])
    labelled = _write(
        tmp_path / "holdout.jsonl",
        [_case("labelled", split="holdout", annotation_status="reviewed")],
    )
    with pytest.raises(
        UnderstandingDataError,
        match="candidates_must_be_development_until_frozen",
    ):
        prepare_understanding_review(labelled, [known], tmp_path / "relabeled")
    with pytest.raises(
        UnderstandingDataError, match="one_to_twenty_references_required"
    ):
        prepare_understanding_review(known, [], tmp_path / "no-reference")
