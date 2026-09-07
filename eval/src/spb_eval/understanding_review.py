"""Offline human-review handoff and dataset freezing, never automatic approval."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, ValidationError, model_validator

from .understanding_dataset import (
    UnderstandingDataError,
    _unique_object,
    build_requests,
    digest,
    load_understanding_cases,
    normalized_input_digest,
)
from .understanding_schema import (
    INTENTS,
    Code,
    Contract,
    Digest,
    UnderstandingCase,
)

REVIEW_VERSION = "qu-review-v1"


class CaseDecision(Contract):
    id: Code
    case_sha256: Digest
    decision: Literal["pending", "approve", "exclude"] = "pending"
    reason_code: Code | None = None

    @model_validator(mode="after")
    def validate_exclusion(self):
        if self.decision == "exclude" and self.reason_code is None:
            raise ValueError("excluded_case_requires_reason")
        return self


class ReviewDecisions(Contract):
    schema_version: Literal["qu-review-v1"] = REVIEW_VERSION
    candidate_sha256: Digest
    reference_sha256: list[Digest] = Field(min_length=1, max_length=20)
    reviewer_id: Code | None = None
    reviewed_at: AwareDatetime | None = None
    annotations_checked: bool = Field(default=False, strict=True)
    semantic_overlap_checked: bool = Field(default=False, strict=True)
    cases: list[CaseDecision] = Field(min_length=1, max_length=1000)


def _audit(
    dataset: Path, references: list[Path]
) -> tuple[dict, list[UnderstandingCase]]:
    if not 1 <= len(references) <= 20:
        raise UnderstandingDataError("one_to_twenty_references_required")
    candidate_hash, cases = load_understanding_cases(dataset)
    # Candidates are not holdout before a human has reviewed them. No implicit split selection.
    if any(case.split != "development" for case in cases):
        raise UnderstandingDataError(
            "candidates_must_be_development_until_frozen"
        )
    reference_hashes = []
    known_ids, known_groups, known_inputs = set(), set(), set()
    for reference in references:
        reference_hash, known = load_understanding_cases(reference)
        if reference_hash in reference_hashes:
            raise UnderstandingDataError("duplicate_reference_dataset")
        reference_hashes.append(reference_hash)
        known_ids.update(case.id for case in known)
        known_groups.update(case.group_id for case in known)
        known_inputs.update(normalized_input_digest(case) for case in known)
    rows = []
    for case in cases:
        issues = []
        for condition, code in (
            (case.id in known_ids, "known_case_id"),
            (case.group_id in known_groups, "known_semantic_group"),
            (
                normalized_input_digest(case) in known_inputs,
                "known_normalized_input",
            ),
            ("seen_smoke" in case.tags, "seen_smoke"),
        ):
            if condition:
                issues.append(code)
        rows.append(
            {
                "id": case.id,
                "case_sha256": digest(case.model_dump(mode="json")),
                "issues": issues,
            }
        )
    return {
        "schema_version": "qu-review-audit-v1",
        "candidate_sha256": candidate_hash,
        "reference_sha256": sorted(reference_hashes),
        "cases": rows,
        "blocked_cases": sum(bool(row["issues"]) for row in rows),
        "limitations": [
            "Exact input and declared group checks cannot discover all semantic overlap.",
            "Only supplied reference datasets are checked; include all previously seen corpora.",
            "Review metadata is a human attestation, not authenticated identity or proof of representativeness.",
        ],
    }, cases


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def prepare_understanding_review(
    dataset: Path, references: list[Path], output_dir: Path
) -> dict:
    audit, _ = _audit(dataset, references)
    decisions = ReviewDecisions(
        candidate_sha256=audit["candidate_sha256"],
        reference_sha256=audit["reference_sha256"],
        cases=[
            CaseDecision(id=row["id"], case_sha256=row["case_sha256"])
            for row in audit["cases"]
        ],
    )
    output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    _write_json(output_dir / "audit.json", audit)
    _write_json(output_dir / "review.json", decisions.model_dump(mode="json"))
    (output_dir / "README.md").write_text(
        "# Understanding 人工审核交接\n\n"
        "这是 pending 审核包，不是已审核 holdout。请人工完成以下检查：\n\n"
        "1. 在原始候选数据中按 ID 检查问题、给定上下文与完整 Gold，不能根据模型输出改标签。\n"
        "2. 检查所有已见语料的同义改写、模板、同会话切片及语义 group；自动检查只覆盖声明与精确重复。\n"
        "3. 修改 review.json：填写 reviewer_id（非敏感别名）、带时区的 reviewed_at，确认两个检查项。\n"
        "4. 每条选择 approve 或 exclude；exclude 必须填写稳定 reason_code。所有 pending 会阻止冻结。\n"
        "5. audit.json 中有冲突的样本不能批准；修正候选 Gold 或参考集后必须重建审核包。\n\n"
        "冻结工具会重新检查源文件、逐条哈希和污染，不信任手工修改后的 audit.json。\n"
        "请勿让自动化填写人类审核声明；本工具也不能认证审核者身份或证明数据有代表性。\n"
        "源文件、审核包及冻结产物可能包含私有内容，应放在 Git 忽略目录，不对外发布。\n",
        encoding="utf-8",
    )
    return {
        "review_dir": str(output_dir),
        "cases": len(audit["cases"]),
        "blocked_cases": audit["blocked_cases"],
        "status": "pending_review",
    }


def _read_review(path: Path) -> ReviewDecisions:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise UnderstandingDataError("review_file_too_large")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
        return ReviewDecisions.model_validate(payload)
    except (ValueError, UnicodeError, RecursionError):
        raise UnderstandingDataError("invalid_review_contract") from None


def freeze_understanding_holdout(
    dataset: Path, references: list[Path], review: Path, output_dir: Path
) -> dict:
    audit, cases = _audit(dataset, references)
    decisions = _read_review(review)
    if (
        decisions.candidate_sha256 != audit["candidate_sha256"]
        or sorted(decisions.reference_sha256) != audit["reference_sha256"]
    ):
        raise UnderstandingDataError("review_dataset_or_references_changed")
    expected = {row["id"]: row for row in audit["cases"]}
    actual = {row.id: row for row in decisions.cases}
    if len(actual) != len(decisions.cases) or actual.keys() != expected.keys():
        raise UnderstandingDataError("review_must_cover_each_case_once")
    if any(
        row.case_sha256 != expected[row.id]["case_sha256"]
        for row in decisions.cases
    ):
        raise UnderstandingDataError("review_case_fingerprint_mismatch")
    if (
        not decisions.reviewer_id
        or decisions.reviewed_at is None
        or decisions.reviewed_at > datetime.now(UTC)
        or not decisions.annotations_checked
        or not decisions.semantic_overlap_checked
    ):
        raise UnderstandingDataError("human_review_attestation_required")
    if any(row.decision == "pending" for row in decisions.cases):
        raise UnderstandingDataError("pending_review_cannot_freeze")
    if any(
        row.decision == "approve" and expected[row.id]["issues"]
        for row in decisions.cases
    ):
        raise UnderstandingDataError("approved_case_has_known_contamination")
    approved = []
    for case in cases:
        if actual[case.id].decision == "approve":
            payload = case.model_dump(mode="json")
            payload.update(split="holdout", annotation_status="reviewed")
            try:
                approved.append(UnderstandingCase.model_validate(payload))
            except ValidationError:
                raise UnderstandingDataError("invalid_frozen_case") from None
    if not approved:
        raise UnderstandingDataError("empty_approved_holdout")
    serialized = "\n".join(case.model_dump_json() for case in approved) + "\n"
    frozen_hash = hashlib.sha256(serialized.encode()).hexdigest()
    requests = build_requests(frozen_hash, approved)
    counts = Counter(
        case.gold.intent
        for case in approved
        if case.gold.intent is not None and case.gold.control == "none"
    )
    manifest = {
        "schema_version": "qu-holdout-freeze-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_sha256": audit["candidate_sha256"],
        "reference_sha256": audit["reference_sha256"],
        "review_sha256": digest(decisions.model_dump(mode="json")),
        "dataset_sha256": frozen_hash,
        "request_sha256": digest(requests),
        "reviewer_id": decisions.reviewer_id,
        "reviewed_at": decisions.reviewed_at.isoformat(),
        "approved_cases": len(approved),
        "excluded_cases": len(cases) - len(approved),
        "intent_support": {intent: counts[intent] for intent in INTENTS},
        "missing_intent_classes": [
            intent for intent in INTENTS if not counts[intent]
        ],
        "gold_hard_values": sum(
            len(case.gold.slot_values)
            for case in approved
            if case.gold.score_slots
        ),
        "limitations": audit["limitations"]
        + [
            "Dataset freeze only: pin code, prompt and runtime configuration separately before inference.",
            "No inference has been performed or authorized by this freeze command.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    with (output_dir / "holdout.jsonl").open("x", encoding="utf-8") as handle:
        handle.write(serialized)
    _write_json(output_dir / "requests.json", requests)
    _write_json(output_dir / "review.json", decisions.model_dump(mode="json"))
    _write_json(output_dir / "manifest.json", manifest)
    return {
        "holdout_dir": str(output_dir),
        "cases": len(approved),
        "dataset_sha256": frozen_hash,
        "missing_intent_classes": manifest["missing_intent_classes"],
    }
