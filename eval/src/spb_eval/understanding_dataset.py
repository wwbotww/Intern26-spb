from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .understanding_schema import (
    ExportManifest,
    UnderstandingCase,
    UnderstandingObservation,
)


class UnderstandingDataError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UnderstandingDataError("duplicate_json_key")
        result[key] = value
    return result


def read_jsonl(path: Path) -> tuple[bytes, list[dict]]:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise UnderstandingDataError("file_too_large")
    raw = path.read_bytes()
    try:
        rows = [
            json.loads(line, object_pairs_hook=_unique_object)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (ValueError, UnicodeError, RecursionError):
        raise UnderstandingDataError("invalid_jsonl") from None
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise UnderstandingDataError("invalid_rows")
    return raw, rows


def normalized_input_digest(case: UnderstandingCase) -> str:
    normalized = case.input.model_dump(mode="json")
    normalized["message"] = "".join(normalized["message"].split()).casefold()
    return digest(normalized)


def load_understanding_cases(
    path: Path, split: str | None = None
) -> tuple[str, list[UnderstandingCase]]:
    raw, rows = read_jsonl(path)
    try:
        cases = [UnderstandingCase.model_validate(row) for row in rows]
    except ValidationError:
        raise UnderstandingDataError("invalid_case_contract") from None
    if len(cases) > 1000:
        raise UnderstandingDataError("too_many_cases")
    seen_ids, seen_inputs, groups = set(), set(), {}
    for case in cases:
        if case.id in seen_ids:
            raise UnderstandingDataError("duplicate_case_id")
        # Whitespace/case variants must not masquerade as unseen examples.
        input_hash = normalized_input_digest(case)
        if input_hash in seen_inputs:
            raise UnderstandingDataError("duplicate_input")
        if case.group_id in groups and groups[case.group_id] != case.split:
            raise UnderstandingDataError("group_split_leakage")
        seen_ids.add(case.id)
        seen_inputs.add(input_hash)
        groups[case.group_id] = case.split
    selected = [case for case in cases if split is None or case.split == split]
    if not selected:
        raise UnderstandingDataError("empty_selected_split")
    return hashlib.sha256(raw).hexdigest(), selected


def build_requests(dataset_hash: str, cases: list[UnderstandingCase]) -> dict:
    return {
        "schema_version": "qu-requests-v1",
        "dataset_sha256": dataset_hash,
        "split": cases[0].split,
        "cases": [
            {"id": case.id, "input": case.input.model_dump(mode="json")}
            for case in cases
        ],
    }


def write_requests(dataset: Path, split: str, output: Path) -> dict:
    dataset_hash, cases = load_understanding_cases(dataset, split)
    request = build_requests(dataset_hash, cases)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(request, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return request


def load_observations(
    path: Path, request: dict
) -> tuple[ExportManifest, dict[str, UnderstandingObservation]]:
    _, rows = read_jsonl(path)
    try:
        manifest = ExportManifest.model_validate(rows[0])
        observations = [
            UnderstandingObservation.model_validate(row) for row in rows[1:]
        ]
    except ValidationError:
        raise UnderstandingDataError("invalid_observation_contract") from None
    if (
        manifest.request_sha256 != digest(request)
        or manifest.dataset_sha256 != request["dataset_sha256"]
        or manifest.split != request["split"]
    ):
        raise UnderstandingDataError("dataset_or_request_mismatch")
    inputs = {case["id"]: digest(case["input"]) for case in request["cases"]}
    by_id = {}
    calls = 0
    for observation in observations:
        if observation.id not in inputs or observation.id in by_id:
            raise UnderstandingDataError("unknown_or_duplicate_observation")
        if observation.input_sha256 != inputs[observation.id]:
            raise UnderstandingDataError("input_fingerprint_mismatch")
        calls += observation.model_call.outcome in {"success", "failure"}
        if (
            manifest.mode == "rules"
            and observation.model_call.outcome != "not_called"
        ):
            raise UnderstandingDataError("model_call_in_rules_run")
        by_id[observation.id] = observation
    if calls > manifest.max_model_calls:
        raise UnderstandingDataError("model_call_budget_violated")
    # Missing rows from an interrupted run are retained as failures by scoring.
    return manifest, by_id
