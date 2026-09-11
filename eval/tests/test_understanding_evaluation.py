from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from spb_eval.cli import main
from spb_eval.understanding_dataset import (
    UnderstandingDataError,
    build_requests,
    digest,
    load_observations,
    load_understanding_cases,
    write_requests,
)
from spb_eval.understanding_metrics import (
    calculate_understanding_metrics,
    slot_fingerprints,
)
from spb_eval.understanding_reporting import (
    compare_understanding,
    write_understanding_report,
)
from spb_eval.understanding_schema import (
    INTENTS,
    ModelCall,
    UnderstandingCase,
    UnderstandingObservation,
)


def _case(name, intent="tracking", **gold):
    return UnderstandingCase(
        id=name,
        group_id=name,
        category="test",
        input={"message": f"synthetic {name}"},
        gold={"intent": intent, **gold},
    )


def _observation(
    case, *, intent=None, slots=None, source="rules", model_call=None
):
    intent = intent or case.gold.intent or case.gold.candidate_intents[0]
    return UnderstandingObservation(
        id=case.id,
        input_sha256=digest(case.input.model_dump(mode="json")),
        status="ok",
        duration_ms=10,
        prediction={
            "intent": intent,
            "candidate_intents": case.gold.candidate_intents or [intent],
            "multi_intent": case.gold.multi_intent,
            "control": case.gold.control,
            "slot_fingerprints": slot_fingerprints(
                case.gold.slot_values if slots is None else slots
            ),
            "missing_slots": case.gold.missing_slots or [],
            "source": source,
            "parser_version": "rules-v1",
            "prompt_version": "prompt-v1" if source == "model" else None,
            "needs_model_fallback": False,
        },
        model_call=model_call or {"outcome": "not_called"},
    )


def _dataset(path, cases):
    path.write_text(
        "\n".join(case.model_dump_json() for case in cases) + "\n",
        encoding="utf-8",
    )
    dataset_hash, loaded = load_understanding_cases(path, "development")
    return build_requests(dataset_hash, loaded)


def _manifest(request, mode="rules"):
    return {
        "type": "manifest",
        "schema_version": "qu-observations-v1",
        "request_sha256": digest(request),
        "dataset_sha256": request["dataset_sha256"],
        "split": "development",
        "mode": mode,
        "transport": "none" if mode == "rules" else "test_transport",
        "created_at": "2026-09-07T00:00:00+00:00",
        "producer_version": "test-v1",
        "rules_version": "rules-v1",
        "parser_version": "parser-v1",
        "prompt_version": None if mode == "rules" else "prompt-v1",
        "implementation_sha256": "b" * 64,
        "configuration_sha256": "d" * 64,
        "prompt_sha256": "c" * 64,
        "model": None if mode == "rules" else "test-model",
        "timeout_seconds": None if mode == "rules" else 8,
        "max_tokens": None if mode == "rules" else 768,
        "max_model_calls": 0 if mode == "rules" else 20,
    }


def _export(path, request, observations, mode="rules"):
    rows = [
        _manifest(request, mode),
        *(obs.model_dump(mode="json") for obs in observations),
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return rows


@pytest.mark.parametrize("excluded", ["product_price", "device_price", None])
def test_macro_f1_counts_missing_prediction_and_false_unknown_separately(excluded):
    labels = [intent for intent in INTENTS if intent != excluded]
    cases = [_case(intent, intent) for intent in labels]
    observations = {case.id: _observation(case) for case in cases}
    observations["policy"] = _observation(cases[0], intent="tracking")
    del observations["delivery_time"]
    summary, rows = calculate_understanding_metrics(cases, observations)
    assert summary["intent"]["accuracy"] == pytest.approx((len(labels) - 2) / len(labels))
    assert summary["intent"]["macro_f1"] == pytest.approx(
        (len(labels) - 3 + 2 / 3) / len(labels)
    )
    assert summary["intent"]["per_class"]["tracking"]["fp"] == 1
    assert summary["intent"]["per_class"]["delivery_time"]["fn"] == 1
    assert summary["intent"]["confusion"]["delivery_time"] == {
        "__missing_or_error__": 1
    }
    assert summary["cases"]["statuses"]["missing"] == 1
    assert summary["model"]["unobserved_case_rows"] == 1
    assert not next(row for row in rows if row["id"] == "delivery_time")[
        "passed"
    ]


def test_slot_micro_f1_penalizes_wrong_extra_and_missing_values():
    cases = [
        _case("wrong", slot_values={"mail_no": "1234567890123"}),
        _case(
            "partial",
            "postage",
            slot_values={"weight_kg": "2.00", "origin": "北京市"},
        ),
        _case("missing", slot_values={"mail_no": "1234567890123"}),
    ]
    observations = {
        "wrong": _observation(
            cases[0], slots={"mail_no": "9999999999999", "origin": "上海市"}
        ),
        "partial": _observation(cases[1], slots={"weight_kg": "2"}),
    }
    summary, _ = calculate_understanding_metrics(cases, observations)
    assert {key: summary["slots"][key] for key in ("tp", "fp", "fn")} == {
        "tp": 1,
        "fp": 2,
        "fn": 3,
    }
    assert summary["slots"]["micro_f1"] == pytest.approx(2 / 7)
    assert summary["slots"]["exact_match_rate"] == 0
    assert summary["slots"]["per_slot"]["weight_kg"]["f1"] == 1


def test_multi_intent_and_control_are_not_arbitrary_single_label_gold():
    multi = _case(
        "multi",
        None,
        candidate_intents=["tracking", "postage"],
        multi_intent=True,
        score_slots=False,
        missing_slots=None,
    )
    control = _case("cancel", "unknown", control="cancel")
    summary, rows = calculate_understanding_metrics(
        [multi, control],
        {case.id: _observation(case) for case in [multi, control]},
    )
    assert summary["intent"]["scored"] == 0
    assert summary["intent"]["classes_with_support"] == 0
    assert summary["slots"]["micro_f1"] is None
    assert summary["multi_intent_accuracy"] == summary["control_accuracy"] == 1
    assert all(row["passed"] for row in rows)
    assert not summary["quality_gate"]["passed"]


def test_failure_fallback_unknown_is_not_a_successful_model_unknown():
    normal, failed, skipped = [
        _case(name, "unknown") for name in ("normal", "failed", "skipped")
    ]
    observations = {
        "normal": _observation(
            normal,
            source="model",
            model_call={
                "outcome": "success",
                "duration_ms": 50,
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
        ),
        "failed": _observation(
            failed,
            model_call={
                "outcome": "failure",
                "duration_ms": 8000,
                "failure_code": "query_model_deadline_exceeded",
            },
        ),
        "skipped": UnderstandingObservation(
            id="skipped",
            input_sha256=digest(skipped.input.model_dump(mode="json")),
            status="skipped",
            error_code="query_model_budget_exhausted",
            duration_ms=0.1,
            model_call={
                "outcome": "budget_exhausted",
                "failure_code": "query_model_budget_exhausted",
            },
        ),
    }
    summary, rows = calculate_understanding_metrics(
        [normal, failed, skipped], observations
    )
    assert summary["model"]["calls"] == 2
    assert summary["model"]["failure_rate_per_call"] == 0.5
    assert (
        summary["model"]["normal_model_unknown"]
        == summary["model"]["failure_fallback_unknown"]
        == 1
    )
    assert summary["model"]["usage"]["total_tokens"] == {
        "known_tokens": 12,
        "unknown_calls": 1,
    }
    assert summary["model"]["latency"]["p95_ms"] == pytest.approx(7602.5)
    assert summary["intent"]["per_class"]["unknown"]["fn"] == 1
    assert rows[1][
        "passed"
    ]  # Safe final behavior can be right despite dependency failure.
    assert not summary["quality_gate"]["checks"]["model_failure_rate"]


@pytest.mark.parametrize(
    "values",
    [
        {"outcome": "not_called", "total_tokens": 0},
        {"outcome": "success", "duration_ms": float("nan")},
        {"outcome": "success", "duration_ms": 10, "total_tokens": True},
        {
            "outcome": "success",
            "duration_ms": 10,
            "total_tokens": 5,
            "prompt_tokens": 5,
            "completion_tokens": 5,
        },
        {"outcome": "failure", "duration_ms": 10},
    ],
)
def test_model_measurement_rejects_untrustworthy_counts(values):
    with pytest.raises(ValidationError):
        ModelCall.model_validate(values)


@pytest.mark.parametrize(
    "change,error",
    [
        ("id", "duplicate_case_id"),
        ("input", "duplicate_input"),
        ("group", "group_split_leakage"),
        ("draft_holdout", "invalid_case_contract"),
        ("seen_holdout", "invalid_case_contract"),
        ("extra_gold", "invalid_case_contract"),
    ],
)
def test_dataset_prevents_leakage_and_unreviewed_holdout(
    tmp_path, change, error
):
    first, second = [
        _case(name).model_dump(mode="json") for name in ("first", "second")
    ]
    if change == "id":
        second["id"] = first["id"]
    elif change == "input":
        second["input"]["message"] = first["input"]["message"].upper() + "  "
    elif change == "group":
        second.update(
            group_id=first["group_id"],
            split="holdout",
            annotation_status="reviewed",
        )
    elif change == "draft_holdout":
        second["split"] = "holdout"
    elif change == "seen_holdout":
        second.update(
            split="holdout", annotation_status="reviewed", tags=["seen_smoke"]
        )
    elif change == "extra_gold":
        second["gold"]["tool_name"] = "unsafe"
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        json.dumps(first) + "\n" + json.dumps(second), encoding="utf-8"
    )
    with pytest.raises(UnderstandingDataError, match=error):
        load_understanding_cases(dataset, "development")


def test_prepare_strips_gold_and_refuses_overwrite(tmp_path):
    dataset, output = tmp_path / "cases.jsonl", tmp_path / "requests.json"
    _dataset(dataset, [_case("one", slot_values={"mail_no": "1234567890123"})])
    request = write_requests(dataset, "development", output)
    assert set(request["cases"][0]) == {"id", "input"}
    assert "1234567890123" not in output.read_text()
    with pytest.raises(FileExistsError):
        write_requests(dataset, "development", output)
    with pytest.raises(UnderstandingDataError, match="empty_selected_split"):
        load_understanding_cases(dataset, "holdout")


@pytest.mark.parametrize(
    "change,error",
    [
        ("request", "dataset_or_request_mismatch"),
        ("input", "input_fingerprint_mismatch"),
        ("duplicate", "unknown_or_duplicate_observation"),
        ("foreign", "unknown_or_duplicate_observation"),
        ("raw", "invalid_observation_contract"),
        ("model_in_rules", "model_call_in_rules_run"),
    ],
)
def test_observations_bind_to_frozen_inputs_and_safe_contract(
    tmp_path, change, error
):
    case = _case("one")
    request = _dataset(tmp_path / "dataset.jsonl", [case])
    path = tmp_path / "observations.jsonl"
    rows = _export(path, request, [_observation(case)])
    if change == "request":
        rows[0]["request_sha256"] = "e" * 64
    elif change == "input":
        rows[1]["input_sha256"] = "f" * 64
    elif change == "duplicate":
        rows.append(rows[1])
    elif change == "foreign":
        rows[1]["id"] = "foreign"
    elif change == "raw":
        rows[1]["prediction"]["question"] = "private-query"
    elif change == "model_in_rules":
        rows[1]["model_call"] = {
            "outcome": "failure",
            "duration_ms": 1,
            "failure_code": "upstream_timeout",
        }
    path.write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
    )
    with pytest.raises(UnderstandingDataError, match=error):
        load_observations(path, request)


def test_compare_recomputes_from_observations_and_retains_missing_rows(
    tmp_path,
):
    dataset, base, exp = (
        tmp_path / name for name in ("cases.jsonl", "base.jsonl", "exp.jsonl")
    )
    improved, regressed = [_case(name) for name in ("improved", "regressed")]
    request = _dataset(dataset, [improved, regressed])
    _export(
        base,
        request,
        [_observation(improved, intent="unknown"), _observation(regressed)],
    )
    _export(exp, request, [_observation(improved)])
    comparison = compare_understanding(dataset, "development", base, exp)
    assert [row["transition"] for row in comparison["transitions"]] == [
        "improved",
        "regressed",
    ]
    assert (
        comparison["experiment"]["summary"]["cases"]["statuses"]["missing"]
        == 1
    )
    output = write_understanding_report(comparison, tmp_path / "out")
    assert (output / "report.md").is_file()
    assert (
        json.loads((output / "review-queue.json").read_text())[0]["id"]
        == "regressed"
    )
    self_comparison = compare_understanding(dataset, "development", base, base)
    assert self_comparison["same_observation_artifact"]
    # Changing only Gold, not messages, must still invalidate old exports.
    improved.gold.intent = "policy"
    _dataset(dataset, [improved, regressed])
    with pytest.raises(
        UnderstandingDataError, match="dataset_or_request_mismatch"
    ):
        compare_understanding(dataset, "development", base, exp)


def test_cli_preserves_reports_before_gate_failure_and_hides_query(
    tmp_path, capsys
):
    case = _case("one", slot_values={"mail_no": "1234567890123"})
    dataset, path = tmp_path / "cases.jsonl", tmp_path / "observations.jsonl"
    request = _dataset(dataset, [case])
    _export(path, request, [])
    assert (
        main(
            [
                "understanding-score",
                "--dataset",
                str(dataset),
                "--observations",
                str(path),
                "--output-dir",
                str(tmp_path / "out"),
                "--fail-on-gate",
            ]
        )
        == 3
    )
    result = json.loads(capsys.readouterr().out)
    saved = (Path(result["report_dir"]) / "report.json").read_text()
    assert "synthetic one" not in saved and "1234567890123" not in saved
    assert (
        json.loads(saved)["summary"]["intent"]["per_class"]["tracking"]["fn"]
        == 1
    )
    assert (
        main(
            [
                "understanding-compare",
                "--dataset",
                str(dataset),
                "--baseline",
                str(path),
                "--experiment",
                str(path),
                "--output-dir",
                str(tmp_path / "compare"),
            ]
        )
        == 0
    )


def test_development_corpus_is_coverage_not_holdout_and_eval_stays_independent():
    root = Path(__file__).resolve().parents[2]
    _, cases = load_understanding_cases(
        root / "eval/datasets/query-understanding-development-v1.jsonl",
        "development",
    )
    assert len(cases) == 48
    assert {case.gold.intent for case in cases if case.gold.intent} == set(
        intent for intent in INTENTS if intent != "product_price"
    )
    assert all(
        case.provenance == "synthetic" and case.annotation_status == "draft"
        for case in cases
    )
    assert sum("seen_smoke" in case.tags for case in cases) == 4
    for path in (root / "eval/src/spb_eval").glob("understanding_*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not any(
                name.startswith(("spb_assistant_api", "langgraph", "sqlite3"))
                for name in names
            )
